"""Cluster jobs: research agents submit heavy work to SLURM while they keep working in their container.

A job runs the experiment's own image on a cluster node (pyxis/enroot,
``srun --container-image``) with the experiment's workspace mounted as /app, so it sees the
agent's code and data and its results land where the agent looks.  The agent drives jobs with
``aurora job`` (:mod:`aurora.compute.cli`), which talks to this manager over a Unix socket;
the manager checks every request against the limits in ``compute.slurm``, submits, follows the
jobs (``squeue``/``scontrol``) and reports them as events.

The manager's own files (job records, batch scripts, the socket) are kept under
``AURORA_HOME/compute``, outside the directory the agents may edit.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import logging
import os
import re
import shlex
import sys
import tempfile
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from aurora.compute import job_client
from aurora.compute.job_client import MAX_WAIT_MINUTES
from aurora.config import SlurmConfig
from aurora.paths import aurora_home

logger = logging.getLogger(__name__)

_POLL_SECONDS = 20.0
_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")
_TIME_RE = re.compile(r"^(?:(\d+)-)?(\d{1,2}):(\d{2}):(\d{2})$")
ACTIVE = {"PENDING", "CONFIGURING", "RUNNING", "COMPLETING", "SUSPENDED", "REQUEUED", "RESIZING"}
# the client agents run (standard library only), mounted into the container for agents that work there
CLIENT_PATH = Path(job_client.__file__).resolve()
CONTAINER_SOCKET_DIR = "/aurora/run"
CONTAINER_CLIENT = "/aurora/lib/aurora_job.py"
# `aurora` inside the container: `aurora job ...` and `aurora papers ...` (whichever clients are mounted)
_WRAPPER = ("#!/bin/sh\n"
            "command -v python3 >/dev/null 2>&1 || { echo \"aurora $1: python3 is missing in this image\" >&2; exit 1; }\n"
            "case \"$1\" in\n"
            "  papers) exec python3 /aurora/lib/aurora_papers.py \"$@\" ;;\n"
            f"  *) exec python3 {CONTAINER_CLIENT} \"$@\" ;;\n"
            "esac\n")


def install_wrapper_command() -> str:
    """The shell command that installs `aurora` in the container (run once a client is mounted)."""
    wrapper = base64.b64encode(_WRAPPER.encode()).decode()
    return (f"( [ -f {CONTAINER_CLIENT} ] || [ -f /aurora/lib/aurora_papers.py ] ) && echo {wrapper} | base64 -d "
            "> /usr/local/bin/aurora && chmod 755 /usr/local/bin/aurora")


def _tag(campaign_dir: Path) -> str:
    return hashlib.sha1(str(Path(campaign_dir).resolve()).encode()).hexdigest()[:8]


def socket_dir_for(campaign_dir: Path) -> Path:
    """The directory of an experiment's job socket (mounted into its container as /aurora/run)."""
    path = aurora_home() / "compute" / "sockets" / _tag(campaign_dir)
    if len(str(path / "jobs.sock")) > 100:           # Unix socket paths are short
        path = Path(tempfile.gettempdir()) / f"aurora-{os.getuid()}" / _tag(campaign_dir)
    return path


def parse_time(text: str) -> int:
    """Seconds in a SLURM time limit: D-HH:MM:SS, HH:MM:SS or minutes."""
    text = str(text).strip()
    if text.isdigit():
        return int(text) * 60
    m = _TIME_RE.match(text)
    if not m:
        raise ValueError(f"time {text!r} is not D-HH:MM:SS, HH:MM:SS or minutes")
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return ((d * 24 + h) * 60 + mi) * 60 + s


def format_time(seconds: int) -> str:
    d, rest = divmod(int(seconds), 86400)
    h, rest = divmod(rest, 3600)
    m, s = divmod(rest, 60)
    return f"{d}-{h:02d}:{m:02d}:{s:02d}" if d else f"{h:02d}:{m:02d}:{s:02d}"


def _elapsed(text: str) -> int:
    """squeue's %M (elapsed): [D-][HH:]MM:SS."""
    text = text.strip()
    days = 0
    if "-" in text:
        d, text = text.split("-", 1)
        days = int(d)
    parts = [int(p) for p in text.split(":") if p.isdigit()]
    while len(parts) < 3:
        parts.insert(0, 0)
    h, m, s = parts[-3:]
    return ((days * 24 + h) * 60 + m) * 60 + s


@dataclass
class ClusterJob:
    id: int                      # 1, 2, ... within the experiment (what the agent uses)
    name: str
    command: str
    gpus: int
    cpus: int
    mem_gb: int
    time_limit: str
    partition: str
    dir: str                     # the job's folder, relative to the workspace (/app)
    slurm_id: str = ""
    job_name: str = ""           # the SLURM job name
    state: str = "SUBMITTING"
    reason: str = ""
    node: str = ""
    exit_code: int | None = None
    submitted_at: float = field(default_factory=time.time)
    started_at: float | None = None
    ended_at: float | None = None
    elapsed_seconds: int = 0

    @property
    def active(self) -> bool:
        return self.state in ACTIVE or self.state == "SUBMITTING"

    @property
    def gpu_hours(self) -> float:
        return self.gpus * self.elapsed_seconds / 3600

    def view(self) -> dict[str, Any]:
        return {**asdict(self), "active": self.active, "gpu_hours": round(self.gpu_hours, 3),
                "log": f"/app/{self.dir}/slurm.out"}


class SlurmCompute:
    """The cluster jobs of one experiment.

    *workspace_dir* is the host directory mounted as the container's /app (it must be on storage
    the cluster nodes see); *image* is the container's Docker image; *backend* runs commands in
    the container (to make the workspace writable for jobs, which run as the cluster user);
    *publish* reports job events.
    """

    def __init__(self, config: SlurmConfig, *, campaign_dir: Path, workspace_dir: Path, image: str,
                 backend: Any = None, publish: Callable[..., Awaitable[None]] | None = None) -> None:
        self.config = config
        self.workspace_dir = Path(workspace_dir).resolve()
        self.image = image
        self._backend = backend
        self._publish = publish
        self.tag = _tag(campaign_dir)
        self.state_dir = aurora_home() / "compute" / "runs" / self.tag
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.jobs: list[ClusterJob] = []
        self._load()
        self.socket_path = socket_dir_for(campaign_dir) / "jobs.sock"
        # research job -> cluster jobs its agent awaits (it ended its turn; it is resumed when they end)
        self.awaited: dict[str, set[int]] = {}
        # check-ins each research job's agent has had in a row while its jobs ran (see check_in_seconds)
        self.check_ins: dict[str, int] = {}
        # the last time asking SLURM failed, and why ("" once it answers again)
        self.slurm_error = ""
        self._server: asyncio.base_events.Server | None = None
        self._poller: asyncio.Task | None = None
        self._image_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        """Serve the agents' requests and follow jobs from an earlier run of this experiment."""
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(FileNotFoundError):
            self.socket_path.unlink()
        self._server = await asyncio.start_unix_server(self._handle, path=str(self.socket_path))
        os.chmod(self.socket_path, 0o600)
        self._image_task = asyncio.create_task(self._prepare_image())
        self._poller = asyncio.create_task(self._poll_loop())
        if self._backend is not None:
            # files the agents create stay writable for jobs, which run as the cluster's user
            await self._exec("printf 'umask 000\\n' > /etc/profile.d/aurora-jobs.sh")
            # `aurora job` for agents that work inside the container (the client is mounted there)
            await self._exec(install_wrapper_command())

    async def stop(self) -> None:
        """Stop serving; cancel the jobs still queued or running if configured."""
        if self.config.cancel_on_finish:
            for job in [j for j in self.jobs if j.active and j.slurm_id]:
                with contextlib.suppress(Exception):
                    await self._cancel(job, "the experiment ended")
        for task in (self._poller, self._image_task):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        if self._server is not None:
            self._server.close()
            with contextlib.suppress(Exception):
                await self._server.wait_closed()
        with contextlib.suppress(FileNotFoundError):
            self.socket_path.unlink()
        self._save()

    # ------------------------------------------------------------------ what the agents get

    def agent_env(self, in_container: bool = False) -> dict[str, str]:
        if in_container:
            return {"AURORA_COMPUTE_SOCKET": f"{CONTAINER_SOCKET_DIR}/{self.socket_path.name}"}
        bin_dir = str(Path(sys.executable).parent)
        return {"AURORA_COMPUTE_SOCKET": str(self.socket_path),
                "PATH": bin_dir + os.pathsep + os.environ.get("PATH", "/usr/bin:/bin")}

    allowed_tools = ["Bash(aurora job *)"]

    def agent_prompt(self, in_container: bool = False) -> str:
        c = self.config
        parts = [c.partition, *[p for p in c.partitions if p != c.partition]]
        limits = [f"at most {c.max_gpus_per_job} GPUs and {c.max_time} per job",
                  f"{c.max_jobs} jobs at a time"]
        if c.max_gpu_hours:
            limits.append(f"{c.max_gpu_hours:g} GPU hours for the whole experiment")
        return (
            "\n## Cluster jobs (SLURM)\n"
            "The container has no GPU. For training, GPU work or anything that runs long, submit a "
            "cluster job; keep the container for writing code and quick tests on small inputs:\n"
            "  aurora job submit --name train --gpus 1 --time 2:00:00 -- python train.py --epochs 20\n"
            "The job runs this container's image on a GPU node, in /app, with this workspace as /app. "
            "Packages you installed in the container are not there: install them into /app (for "
            "example a venv in /app/.venv) or in the job's command. Its output goes to "
            "/app/.aurora/jobs/<id>-<name>/slurm.out.\n"
            "  aurora job status [<id>]            the jobs and their states\n"
            "  aurora job logs <id> [--tail 200]   its output\n"
            "  aurora job cancel <id>\n"
            "  aurora job await <id> [<id> ...]    then END YOUR TURN: you are resumed when they end\n"
            f"  aurora job wait <id> [--minutes N]   wait in place for a short job (at most {MAX_WAIT_MINUTES} min)\n"
            "To wait for a job that takes longer than a few minutes, run `aurora job await <ids>` and end "
            "your turn with a short note of what you are waiting for and what you will do next. Nothing "
            "runs while you wait and your job's time does not count. You are resumed in this conversation "
            f"when the jobs end, and in between now and then (after {c.check_in_minutes[0]:g} minutes at first, "
            f"later every {c.check_in_minutes[-1]:g} minutes) with their state: if a job is still pending, its reason says why; if it cannot start "
            "as submitted (more time, GPUs or memory than allowed), cancel it and submit it again; if "
            "SLURM did not answer, the jobs may still be running. Then await again, or go on. Do not poll "
            "with sleep.\n"
            + ("Run `aurora job` as a plain command. " if in_container
               else "Run `aurora job` directly, not inside docker exec. ")
            + "Options: --gpus, --time (HH:MM:SS), "
            f"--cpus, --mem (GB), --partition ({', '.join(p for p in parts if p) or 'the default'}). "
            f"Limits: {'; '.join(limits)}. Jobs wait in a queue first; use the time to prepare the "
            "next step. Jobs keep running when your session ends (unless the experiment ends): "
            "name the job IDs and their output in your report.\n"
        )

    # ------------------------------------------------------------------ requests

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            line = await reader.readline()
            request = json.loads(line or b"{}")
            op = request.get("op")
            handlers = {"submit": self.submit, "status": self.status, "logs": self.logs,
                        "cancel": self.cancel, "wait": self.wait, "await": self.await_jobs}
            if op not in handlers:
                raise ValueError(f"unknown request {op!r}")
            args = {k: v for k, v in request.items() if k != "op" and (k != "agent_job" or op == "await")}
            response: dict[str, Any] = {"ok": True, **await handlers[op](**args)}
        except (ValueError, TypeError, KeyError, RuntimeError) as exc:
            response = {"ok": False, "error": str(exc)}
        except Exception as exc:
            logger.exception("cluster job request failed")
            response = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        writer.write((json.dumps(response) + "\n").encode())
        with contextlib.suppress(Exception):
            await writer.drain()
            writer.close()

    def _job(self, ref: Any) -> ClusterJob:
        text = str(ref).strip()
        for job in self.jobs:
            if text in (str(job.id), job.slurm_id, job.name):
                return job
        raise ValueError(f"no job {text!r}; see `aurora job status`")

    async def submit(self, command: str, name: str = "", gpus: int = 0, time_limit: str = "",
                     partition: str = "", cpus: int | None = None, mem_gb: int | None = None) -> dict[str, Any]:
        c = self.config
        command = (command or "").strip()
        if not command:
            raise ValueError("no command: aurora job submit [options] -- <command>")
        name = name or "job"
        if not _NAME_RE.match(name):
            raise ValueError("--name may use letters, digits, '.', '_' and '-' (at most 40)")
        gpus = int(gpus)
        if not 0 <= gpus <= c.max_gpus_per_job:
            raise ValueError(f"--gpus must be 0 to {c.max_gpus_per_job}")
        partition = partition or c.partition
        if partition and partition != c.partition and partition not in c.partitions:
            raise ValueError(f"partition {partition!r} not allowed; use one of {[c.partition, *c.partitions]}")
        # no time given: the default, but never more than the most allowed
        seconds = parse_time(time_limit) if time_limit else min(parse_time(c.default_time), parse_time(c.max_time))
        if seconds > parse_time(c.max_time):
            raise ValueError(f"--time is at most {c.max_time}")
        per = max(gpus, 1)
        cpus = int(cpus or c.cpus_per_gpu * per)
        mem_gb = int(mem_gb or c.mem_per_gpu_gb * per)
        if not 1 <= cpus <= c.cpus_per_gpu * per:
            raise ValueError(f"--cpus is at most {c.cpus_per_gpu * per} for {gpus} GPU(s)")
        if not 1 <= mem_gb <= c.mem_per_gpu_gb * per:
            raise ValueError(f"--mem is at most {c.mem_per_gpu_gb * per} GB for {gpus} GPU(s)")
        async with self._lock:
            active = [j for j in self.jobs if j.active]
            if len(active) >= c.max_jobs:
                raise ValueError(f"{len(active)} jobs are queued or running (at most {c.max_jobs}); "
                                 "wait for one to end or cancel one")
            used = sum(j.gpu_hours for j in self.jobs)
            if c.max_gpu_hours and gpus and used >= c.max_gpu_hours:
                raise ValueError(f"the experiment's {c.max_gpu_hours:g} GPU hours are used up ({used:.1f})")
            n = max((j.id for j in self.jobs), default=0) + 1
            job = ClusterJob(id=n, name=name, command=command, gpus=gpus, cpus=cpus, mem_gb=mem_gb,
                             time_limit=format_time(seconds), partition=partition,
                             dir=f".aurora/jobs/{n}-{name}")
            job.job_name = f"{c.job_name_prefix}-{self.tag}-{n}-{name}"
            self.jobs.append(job)
            self._save()
        try:
            image = await self._image()
            await self._make_writable(job)
            job.slurm_id = await self._sbatch(job, image)
        except Exception as exc:
            job.state, job.reason, job.ended_at = "FAILED", f"not submitted: {exc}", time.time()
            self._save()
            raise RuntimeError(f"could not submit: {exc}") from exc
        job.state = "PENDING"
        self._save()
        await self._emit("compute_job_submitted", job)
        self._wake()
        return {"job": job.view(), "message": (
            f"Submitted job {job.id} ({job.name}) as SLURM job {job.slurm_id}: {gpus} GPU(s), "
            f"{cpus} CPUs, {mem_gb} GB, up to {job.time_limit}. Output: /app/{job.dir}/slurm.out")}

    async def status(self, job: Any = None) -> dict[str, Any]:
        jobs = [self._job(job)] if job not in (None, "") else self.jobs
        return {"jobs": [j.view() for j in jobs], "slurm_error": self.slurm_error,
                "gpu_hours": round(sum(j.gpu_hours for j in self.jobs), 3)}

    async def logs(self, job: Any, tail: int = 200) -> dict[str, Any]:
        j = self._job(job)
        path = self.workspace_dir / j.dir / "slurm.out"
        if not path.exists():
            return {"job": j.view(), "text": "", "message": "no output yet"}
        lines = path.read_text(errors="replace").splitlines()
        tail = max(1, min(int(tail), 2000))
        return {"job": j.view(), "text": "\n".join(lines[-tail:]), "lines": len(lines)}

    async def cancel(self, job: Any, reason: str = "cancelled by the agent") -> dict[str, Any]:
        j = self._job(job)
        if not j.active:
            return {"job": j.view(), "message": f"job {j.id} has already ended ({j.state})"}
        await self._cancel(j, reason)
        return {"job": j.view(), "message": f"cancelling job {j.id} (SLURM {j.slurm_id})"}

    async def wait(self, job: Any, minutes: float = 5) -> dict[str, Any]:
        j = self._job(job)
        deadline = time.time() + max(0.0, min(float(minutes), MAX_WAIT_MINUTES)) * 60
        while j.active and time.time() < deadline:
            self._wake()
            await asyncio.sleep(min(_POLL_SECONDS, max(1.0, deadline - time.time())))
        return {"job": j.view(), "message": (f"job {j.id} {j.state.lower()}" if not j.active
                                             else f"job {j.id} is still {j.state.lower()}")}

    async def await_jobs(self, jobs: list[Any], agent_job: str = "") -> dict[str, Any]:
        """The agent ends its turn and is resumed when *jobs* have ended (see TerminalIteration)."""
        found = [self._job(j) for j in jobs]
        active = [j for j in found if j.active]
        if not active:
            return {"jobs": [j.view() for j in found],
                    "message": "These jobs have already ended; go on with their results."}
        if not agent_job:
            raise ValueError("await works only in a research job's session; use `aurora job wait`")
        self.awaited.setdefault(agent_job, set()).update(j.id for j in active)
        minutes = self.check_in_seconds(agent_job) / 60
        return {"jobs": [j.view() for j in found], "message": (
            "Now end your turn with a short note of what you are waiting for. You will be resumed in "
            "this conversation when " + ("this job ends" if len(active) == 1 else "these jobs end")
            + f", or in {minutes:g} minutes with their state if they are still queued or running.")}

    def awaiting(self, agent_job: str) -> list[ClusterJob]:
        """The cluster jobs *agent_job*'s agent awaits (ended ones included, until taken)."""
        ids = self.awaited.get(agent_job, set())
        return [j for j in self.jobs if j.id in ids]

    def take_awaited(self, agent_job: str) -> list[ClusterJob]:
        jobs = self.awaiting(agent_job)
        self.awaited.pop(agent_job, None)
        return jobs

    def check_in_seconds(self, agent_job: str) -> float:
        """How long *agent_job*'s agent waits before it is shown its jobs' state: short at first,
        longer the more check-ins in a row found them still running (check_in_minutes)."""
        steps = self.config.check_in_minutes
        return steps[min(self.check_ins.get(agent_job, 0), len(steps) - 1)] * 60

    def checked_in(self, agent_job: str, *, jobs_ended: bool) -> None:
        """Count a check-in; when awaited jobs ended (or the researcher wrote), start over."""
        self.check_ins[agent_job] = 0 if jobs_ended else self.check_ins.get(agent_job, 0) + 1

    # ------------------------------------------------------------------ SLURM

    async def _slurm(self, *args: str, timeout: float = 60.0) -> tuple[int, str, str]:
        if self.config.submit_via:
            cmd = [*self.config.submit_via, shlex.join(args)]   # a remote shell parses one string
        else:
            cmd = list(args)
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout)
        except TimeoutError:
            proc.kill()
            return 124, "", f"{args[0]} timed out"
        return proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")

    def _script(self, job: ClusterJob, image: str) -> str:
        c = self.config
        job_dir = self.workspace_dir / job.dir
        lines = ["#!/bin/bash", f"#SBATCH --job-name={job.job_name}",
                 f"#SBATCH --output={job_dir}/slurm.out", f"#SBATCH --time={job.time_limit}",
                 f"#SBATCH --cpus-per-task={job.cpus}", f"#SBATCH --mem={job.mem_gb}G",
                 "#SBATCH --nodes=1", "#SBATCH --ntasks=1"]
        if job.partition:
            lines.append(f"#SBATCH --partition={job.partition}")
        if job.gpus:
            lines.append(f"#SBATCH --gres=gpu:{job.gpus}")
        if c.account:
            lines.append(f"#SBATCH --account={c.account}")
        if c.qos:
            lines.append(f"#SBATCH --qos={c.qos}")
        lines += [f"#SBATCH {a}" for a in c.extra_args]
        inner = f"cd /app && bash /app/{job.dir}/run.sh; code=$?; echo $code > /app/{job.dir}/exit_code; exit $code"
        lines += [
            "export NVIDIA_VISIBLE_DEVICES=${NVIDIA_VISIBLE_DEVICES:-all}",
            "srun " + shlex.join([f"--container-image={image}",
                                  f"--container-mounts={self.workspace_dir}:/app",
                                  "--container-workdir=/app", "--no-container-mount-home",
                                  "bash", "-c", inner]),
        ]
        return "\n".join(lines) + "\n"

    async def _sbatch(self, job: ClusterJob, image: str) -> str:
        job_dir = self.workspace_dir / job.dir
        job_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(job_dir, 0o777)
        (job_dir / "run.sh").write_text(f"#!/bin/bash\n# job {job.id} ({job.name})\n{job.command}\n")
        script = self.state_dir / f"{job.id}-{job.name}.sbatch"
        script.write_text(self._script(job, image))
        script.chmod(0o644)
        code, out, err = await self._slurm("sbatch", "--parsable", str(script))
        slurm_id = out.strip().split(";")[0].strip()
        if code != 0 or not slurm_id.isdigit():
            raise RuntimeError((err or out).strip()[-500:] or f"sbatch exited with {code}")
        return slurm_id

    async def _cancel(self, job: ClusterJob, reason: str) -> None:
        # the name guards against cancelling someone else's job by a wrong ID
        code, out, err = await self._slurm("scancel", f"--name={job.job_name}", job.slurm_id)
        if code != 0:
            raise RuntimeError((err or out).strip()[-300:] or "scancel failed")
        job.reason = reason
        self._save()
        self._wake()

    async def _poll_loop(self) -> None:
        self._wakeup = asyncio.Event()
        while True:
            if any(j.active and j.slurm_id for j in self.jobs):
                try:
                    await self._poll()
                except Exception:
                    logger.warning("following cluster jobs failed", exc_info=True)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wakeup.wait(), _POLL_SECONDS)
            self._wakeup.clear()

    def _wake(self) -> None:
        wakeup = getattr(self, "_wakeup", None)
        if wakeup is not None:
            wakeup.set()

    async def _poll(self) -> None:
        active = {j.slurm_id: j for j in self.jobs if j.active and j.slurm_id}
        code, out, err = await self._slurm("squeue", "-h", "-o", "%i|%T|%M|%r|%N", "-j", ",".join(active))
        # a job SLURM has forgotten (ended a while ago) makes squeue fail when asked for alone
        if code != 0 and "Invalid job id" not in err:
            # no answer is not an answer: the jobs keep their states until SLURM answers again
            error = (err or out).strip()[-300:] or f"squeue exited with {code}"
            if not self.slurm_error:
                logger.warning("Could not ask SLURM about the jobs: %s", error)
            self.slurm_error = f"{time.strftime('%H:%M')}: {error}"
            return
        self.slurm_error = ""
        seen: set[str] = set()
        for line in out.splitlines() if code == 0 else []:
            parts = line.strip().split("|")
            if len(parts) < 5 or parts[0] not in active:
                continue
            job = active[parts[0]]
            seen.add(parts[0])
            before = job.state
            job.state, job.reason, job.node = parts[1], parts[3] if parts[3] != "None" else "", parts[4]
            job.elapsed_seconds = _elapsed(parts[2])
            if job.state == "RUNNING" and job.started_at is None:
                job.started_at = time.time() - job.elapsed_seconds
            if job.state != before:
                await self._emit("compute_job_update", job)
        for slurm_id, job in active.items():
            if slurm_id not in seen:
                await self._finished(job)
        self._save()


    async def _finished(self, job: ClusterJob) -> None:
        """A job left the queue: find out how it ended."""
        code, out, _ = await self._slurm("scontrol", "show", "job", "-o", job.slurm_id)
        fields = dict(re.findall(r"(\w+)=(\S*)", out)) if code == 0 else {}
        state = fields.get("JobState", "")
        if state in ACTIVE:
            return           # squeue missed it; still running
        exit_file = self.workspace_dir / job.dir / "exit_code"
        exit_code: int | None = None
        if exit_file.exists():
            with contextlib.suppress(ValueError):
                exit_code = int(exit_file.read_text().strip())
        elif fields.get("ExitCode", "").split(":")[0].isdigit():
            exit_code = int(fields["ExitCode"].split(":")[0])
        if not state:
            state = "ENDED" if exit_code is None else ("COMPLETED" if exit_code == 0 else "FAILED")
        if fields.get("RunTime"):
            job.elapsed_seconds = _elapsed(fields["RunTime"])
        job.state, job.exit_code, job.ended_at = state, exit_code, time.time()
        if fields.get("Reason") and fields["Reason"] != "None":
            job.reason = job.reason or fields["Reason"]
        await self._emit("compute_job_ended", job)

    # ------------------------------------------------------------------ image and files

    async def _image(self) -> str:
        if self._image_task is None:
            self._image_task = asyncio.create_task(self._prepare_image())
        return await asyncio.shield(self._image_task)

    async def _prepare_image(self) -> str:
        """The container's image as a squashfs file on shared storage (made once per image ID)."""
        if self.image.endswith(".sqsh") and Path(self.image).exists():
            return self.image
        image_dir = Path(self.config.image_dir).expanduser() if self.config.image_dir \
            else aurora_home() / "compute" / "images"
        image_dir.mkdir(parents=True, exist_ok=True)
        proc = await asyncio.create_subprocess_exec("docker", "image", "inspect", "-f", "{{.Id}}", self.image,
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await proc.communicate()
        if proc.returncode:
            raise RuntimeError(f"image {self.image} not found: {err.decode().strip()}")
        digest = out.decode().strip().split(":")[-1][:12]
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", self.image)
        target = image_dir / f"{safe}-{digest}.sqsh"
        if target.exists():
            return str(target)
        tmp = target.with_suffix(f".{os.getpid()}.tmp")
        logger.info("Saving image %s for the cluster nodes (%s)", self.image, target)
        proc = await asyncio.create_subprocess_exec("enroot", "import", "-o", str(tmp), f"dockerd://{self.image}",
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        out, _ = await proc.communicate()
        if proc.returncode or not tmp.exists():
            with contextlib.suppress(FileNotFoundError):
                tmp.unlink()
            raise RuntimeError(f"enroot import of {self.image} failed: {out.decode(errors='replace')[-400:]}")
        tmp.chmod(0o644)
        tmp.replace(target)
        return str(target)

    async def _exec(self, command: str) -> None:
        with contextlib.suppress(Exception):
            await self._backend.exec_command(command, timeout=300)

    async def _make_writable(self, job: ClusterJob) -> None:
        """Jobs run as the cluster's user: let them write the workspace (files are root's in the container)."""
        if self._backend is not None:
            await self._exec("find /app -xdev ! -perm -o=w ! -type l -exec chmod a+rwX {} +")

    # ------------------------------------------------------------------ records

    def _load(self) -> None:
        path = self.state_dir / "jobs.json"
        if path.exists():
            with contextlib.suppress(Exception):
                self.jobs = [ClusterJob(**d) for d in json.loads(path.read_text())]

    def _save(self) -> None:
        path = self.state_dir / "jobs.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps([asdict(j) for j in self.jobs], indent=1))
        tmp.replace(path)

    async def _emit(self, name: str, job: ClusterJob) -> None:
        if self._publish is not None:
            await self._publish(name, **job.view())


def saved_jobs(campaign_dir: Path) -> list[ClusterJob]:
    """The cluster jobs recorded for an experiment (also after it ended)."""
    tag = hashlib.sha1(str(Path(campaign_dir).resolve()).encode()).hexdigest()[:8]
    path = aurora_home() / "compute" / "runs" / tag / "jobs.json"
    if not path.exists():
        return []
    try:
        return [ClusterJob(**d) for d in json.loads(path.read_text())]
    except (ValueError, TypeError):
        return []
