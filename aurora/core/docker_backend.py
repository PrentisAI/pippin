"""Docker execution backend: one container per campaign, shared by all its phases.

The container runs ``sleep infinity``; the agents work in ``/app`` through
``docker exec``.  When the campaign ends, ``/app`` is copied to
``<campaign>/workspace`` and the container is removed.

Acceptance tests (``sandbox.tests_dir``) are copied into ``/tests`` only while
they run and removed right after, so the agents never see them.
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


async def _run(*args: str, timeout: float = 120.0, stdin: bytes | None = None) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *args, stdin=asyncio.subprocess.PIPE if stdin is not None else None,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(stdin), timeout=timeout)
    except TimeoutError:
        proc.kill()
        return 124, "", f"`{' '.join(args[:3])} ...` timed out after {timeout:g}s"
    return proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")


class DockerBackend:
    """A container for one campaign.

    Parameters
    ----------
    docker_image:
        Image to run; pulled when it is not present locally.
    cpus, memory_mb:
        Resource limits of the container.
    allow_network:
        Internet access from the container.
    tests_dir:
        Host directory with acceptance tests (``test.sh`` or pytest files).
    workspace_dir:
        Host directory mounted as /app: the container's files are written straight to it.
        Empty: /app lives in the container and is copied out (:meth:`extract_workspace`).
    agent:
        Run research agents inside the container (experimental): ``{"claude": <path of the
        claude executable>, "upstream": <API base URL>, "key": <API key>}``.  The key stays in a
        small proxy container (:mod:`aurora.core.key_proxy`); the campaign container gets a random
        token and reaches only that proxy on an internal network (and the internet if allowed).
    """

    def __init__(self, docker_image: str = "python:3.11-slim", *, cpus: float = 4.0, memory_mb: int = 8192,
                 allow_network: bool = True, tests_dir: str = "", pull_timeout: float = 1800.0,
                 campaign_dir: str = "", workspace_dir: str = "", agent: dict[str, str] | None = None) -> None:
        self._campaign_dir = campaign_dir
        self._workspace_dir = workspace_dir
        self._agent = agent
        self._network: str | None = None
        self._proxy_id: str | None = None
        # set by setup() when agents can run in the container: how they reach the API
        self.agent_env: dict[str, str] | None = None
        self._image = docker_image
        self._cpus = cpus
        self._memory_mb = memory_mb
        self._allow_network = allow_network
        self._tests_dir = tests_dir
        self._pull_timeout = pull_timeout
        self.container_id: str | None = None

    async def setup(self) -> None:
        code, _, _ = await _run("docker", "image", "inspect", self._image, timeout=30)
        if code != 0:
            logger.info("Pulling image %s ...", self._image)
            code, _, err = await _run("docker", "pull", self._image, timeout=self._pull_timeout)
            if code != 0:
                raise RuntimeError(f"Could not pull Docker image {self._image}: {err.strip()}")
        mount: list[str] = []
        if self._workspace_dir:
            Path(self._workspace_dir).mkdir(parents=True, exist_ok=True)
            mount = ["-v", f"{Path(self._workspace_dir).resolve()}:/app", "--label", "aurora.mounted=1"]
        network = "bridge" if self._allow_network else "none"
        token = ""
        if self._agent:
            token = secrets.token_hex(24)
            await self._start_proxy(token)
            campaign = str(Path(self._campaign_dir).resolve())
            home = Path(campaign) / "agent_home"
            home.mkdir(parents=True, exist_ok=True)
            mount += ["-v", f"{self._agent['claude']}:/aurora/bin/claude:ro",
                      "-v", f"{campaign}:{campaign}:ro",          # plans and earlier phases, at their host paths
                      "-v", f"{home}:/aurora/home"]               # the agents' sessions, kept for resume
            network = self._network       # first the proxy's network, so its name resolves
        code, out, err = await _run(
            "docker", "run", "-d", "--init",
            "--cpus", str(self._cpus), "--memory", f"{self._memory_mb}m",
            "--network", network,
            "--label", "aurora.campaign=1", "--label", f"aurora.campaign_dir={self._campaign_dir}",
            *mount, "-w", "/app", self._image, "sleep", "infinity", timeout=120)
        if code != 0:
            raise RuntimeError(f"Could not start a container from {self._image}: {err.strip()}")
        self.container_id = out.strip()[:12]
        await self.exec_command("mkdir -p /app")
        logger.info("Container %s started from %s", self.container_id, self._image)
        if self._agent:
            if self._allow_network:       # then the internet; without it, only the proxy is reachable
                await _run("docker", "network", "connect", "bridge", self.container_id, timeout=60)
            code, out, err = await self.exec_command("/aurora/bin/claude --version", timeout=60)
            if code == 0:
                self.agent_env = {"ANTHROPIC_BASE_URL": f"http://{PROXY_ALIAS}:8080", "ANTHROPIC_AUTH_TOKEN": token,
                                  "ANTHROPIC_API_KEY": "", "HOME": "/aurora/home"}
            else:
                logger.warning("Claude Code does not run in %s (%s); agents work from the host instead",
                               self._image, (err or out).strip()[:200])

    @property
    def resources(self) -> tuple[float, int]:
        """The container's current (cpus, memory_mb) limits."""
        return self._cpus, self._memory_mb

    async def update_resources(self, cpus: float | None = None, memory_mb: int | None = None) -> None:
        """Change the limits of the running container (``docker update``), keeping what runs in it."""
        cpus = self._cpus if cpus is None else cpus
        memory_mb = self._memory_mb if memory_mb is None else memory_mb
        if self.container_id:
            # the swap limit must be given with the memory limit, or raising the memory above the
            # old swap limit fails; twice the memory is Docker's own default
            code, _, err = await _run("docker", "update", "--cpus", str(cpus), "--memory", f"{memory_mb}m",
                                      "--memory-swap", f"{2 * memory_mb}m", self.container_id, timeout=60)
            if code != 0:
                raise RuntimeError(f"docker update failed: {err.strip()}")
        self._cpus, self._memory_mb = cpus, memory_mb
        logger.info("Container %s now has %g CPUs and %d MB memory", self.container_id, cpus, memory_mb)

    @property
    def mounted(self) -> bool:
        """/app is the host's workspace directory (nothing needs copying)."""
        return bool(self._workspace_dir)

    async def give_back(self) -> None:
        """Make the files the container wrote in the mounted /app yours (they are root's)."""
        if self.container_id and (self.mounted or self._agent):
            await _give_back(self.container_id)

    async def _start_proxy(self, token: str) -> None:
        """The key proxy: its own container, on the bridge (to reach the API) and an internal network."""
        self._network = f"aurora-{secrets.token_hex(5)}"
        labels = ["--label", "aurora.campaign=1", "--label", f"aurora.campaign_dir={self._campaign_dir}"]
        code, _, err = await _run("docker", "network", "create", "--internal", *labels, self._network, timeout=60)
        if code != 0:
            raise RuntimeError(f"Could not create the campaign network: {err.strip()}")
        code, _, _ = await _run("docker", "image", "inspect", PROXY_IMAGE, timeout=30)
        if code != 0:
            await _run("docker", "pull", PROXY_IMAGE, timeout=self._pull_timeout)
        # the key goes in through a file only this user can read, not the command line
        with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as fh:
            fh.write(f"AURORA_UPSTREAM={self._agent['upstream']}\nAURORA_KEY={self._agent['key']}\n"
                     f"AURORA_TOKEN={token}\n")
            env_file = fh.name
        os.chmod(env_file, 0o600)
        try:
            code, out, err = await _run(
                "docker", "run", "-d", "--init", "--memory", "256m", "--cpus", "0.5", "--network", "bridge",
                *labels, "--label", "aurora.role=proxy", "--env-file", env_file,
                "-v", f"{Path(__file__).with_name('key_proxy.py')}:/aurora/key_proxy.py:ro",
                PROXY_IMAGE, "python3", "/aurora/key_proxy.py", timeout=120)
        finally:
            os.unlink(env_file)
        if code != 0:
            raise RuntimeError(f"Could not start the API proxy: {err.strip()}")
        self._proxy_id = out.strip()[:12]
        await _run("docker", "network", "connect", "--alias", PROXY_ALIAS, self._network, self._proxy_id, timeout=60)

    async def teardown(self) -> None:
        if self.container_id:
            await self.give_back()
            await _run("docker", "rm", "-f", self.container_id, timeout=60)
            logger.info("Container %s removed", self.container_id)
            self.container_id = None
        if self._proxy_id:
            await _run("docker", "rm", "-f", self._proxy_id, timeout=60)
            self._proxy_id = None
        if self._network:
            await _run("docker", "network", "rm", self._network, timeout=60)
            self._network = None

    async def exec_command(self, command: str, *, timeout: float = 120.0,
                           workdir: str | None = None) -> tuple[int, str, str]:
        if not self.container_id:
            raise RuntimeError("Container not started")
        return await _run("docker", "exec", "-w", workdir or "/app", self.container_id, "sh", "-c", command,
                          timeout=timeout)

    async def run_verifier(self, *, timeout: float = 600.0) -> tuple[bool, float, str]:
        """Run the acceptance tests; passed means exit code 0."""
        if not self._tests_dir:
            return True, 1.0, "no acceptance tests configured"
        tests = Path(self._tests_dir).expanduser().resolve()
        if not tests.is_dir():
            return False, 0.0, f"tests_dir not found: {tests}"
        code, _, err = await _run("docker", "cp", f"{tests}/.", f"{self.container_id}:/tests", timeout=60)
        if code != 0:
            return False, 0.0, f"Could not copy the tests: {err}"
        try:
            cmd = ("if [ -f /tests/test.sh ]; then bash /tests/test.sh; "
                   "else python -m pytest -q /tests; fi")
            code, out, err = await self.exec_command(cmd + " 2>&1", timeout=timeout)
        finally:
            await self.exec_command("rm -rf /tests")
        passed = code == 0
        return passed, 1.0 if passed else 0.0, out + err

    async def extract_workspace(self, dest: Path) -> bool:
        if not self.container_id:
            return False
        if self.mounted:            # the files are on the host already
            return True
        dest.mkdir(parents=True, exist_ok=True)
        code, _, err = await _run("docker", "cp", f"{self.container_id}:/app/.", str(dest), timeout=600)
        if code != 0:
            logger.warning("Could not copy /app out of the container: %s", err.strip())
        return code == 0


PROXY_IMAGE = "python:3.11-slim"
PROXY_ALIAS = "aurora-proxy"


async def _give_back(container_id: str) -> None:
    """Make what the container wrote on the host (/app, and the agents' sessions) yours again."""
    code, _, err = await _run("docker", "exec", container_id, "sh", "-c",
                              f"chown -R {os.getuid()}:{os.getgid()} /app; "
                              f"[ ! -d /aurora/home ] || chown -R {os.getuid()}:{os.getgid()} /aurora/home",
                              timeout=900)
    if code != 0:
        logger.warning("Could not hand the files in /app back to you: %s", err.strip())


_HOST_LIMITS: tuple[int, int] | None = None


async def host_limits() -> tuple[int, int]:
    """CPUs and memory (MB) of the machine Docker runs on; this machine's if Docker cannot say."""
    global _HOST_LIMITS
    if _HOST_LIMITS is None:
        cpus, mem = os.cpu_count() or 1, 0
        code, out, _ = await _run("docker", "info", "--format", "{{.NCPU}} {{.MemTotal}}", timeout=30)
        try:
            if code == 0:
                n, m = out.split()
                cpus, mem = int(n), int(m) // (1024 * 1024)
        except ValueError:
            pass
        if not mem:
            try:
                mem = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") // (1024 * 1024)
            except (ValueError, OSError):
                mem = 0
        _HOST_LIMITS = (cpus, mem)
    return _HOST_LIMITS


async def remove_orphaned_containers(under: Path) -> int:
    """Remove campaign containers whose campaign directory is under *under*.

    The web console calls this at start-up: no experiment survives a server
    restart, so any such container was left behind by a crash.
    """
    code, out, _ = await _run("docker", "ps", "-a", "--filter", "label=aurora.campaign=1",
                              "--format",
                              '{{.ID}} {{.Label "aurora.mounted"}}{{.Label "aurora.role"}} {{.Label "aurora.campaign_dir"}}',
                              timeout=30)
    if code != 0:
        return 0
    removed = 0
    root = str(Path(under).resolve())
    for line in out.splitlines():
        cid, kind, campaign_dir = (line.split(" ", 2) + ["", ""])[:3]
        if campaign_dir and (campaign_dir == root or campaign_dir.startswith(root + "/")):
            if kind == "proxy":
                pass                    # an API proxy: nothing to keep
            elif kind == "1":
                # the files are on the host already; only make them yours
                await _run("docker", "start", cid, timeout=60)
                await _give_back(cid)
            else:
                # keep what the agents produced before the crash: copy /app out first
                dest = Path(campaign_dir) / "workspace"
                dest.mkdir(parents=True, exist_ok=True)
                code, _, err = await _run("docker", "cp", f"{cid}:/app/.", str(dest), timeout=600)
                if code != 0:
                    logger.warning("Could not save /app of the orphaned container %s: %s", cid, err.strip())
            await _run("docker", "rm", "-f", cid, timeout=60)
            removed += 1
    code, out, _ = await _run("docker", "network", "ls", "--filter", "label=aurora.campaign=1", "--format",
                              '{{.Name}} {{.Label "aurora.campaign_dir"}}', timeout=30)
    for line in out.splitlines() if code == 0 else []:
        name, _, campaign_dir = line.partition(" ")
        if campaign_dir == root or campaign_dir.startswith(root + "/"):
            await _run("docker", "network", "rm", name, timeout=60)
    return removed
