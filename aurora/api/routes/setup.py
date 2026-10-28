"""The first minutes: is everything a run needs there, a demo that needs nothing, and setting the key.

* ``GET /api/setup``: what a run needs (a model key, Claude Code, Docker) and what helps (the
  container image, cluster jobs, notifications, OpenAlex), each with how to fix it;
* ``PUT /api/setup/key``: the server's OpenRouter key, without accounts (written to the console's
  ``.env``, readable only by its user);
* ``POST /api/demo``: a project with a run of scripted models, finished in seconds (no key, no Docker).
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import sys
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from aurora.auth.guard import current_user

router = APIRouter(prefix="/api", tags=["setup"])


def _run(cmd: list[str], timeout: float = 15) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or p.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)


DOCKER_APP = "/Applications/Docker.app"
DOCKER_BIN = DOCKER_APP + "/Contents/Resources/bin"
# what a run needs of Docker Desktop's commands: the CLI, and the helpers it calls to pull images
DOCKER_COMMANDS = ("docker", "docker-credential-desktop", "docker-credential-osxkeychain")


def _link_dir() -> str:
    """Where to link Docker Desktop's commands: Homebrew's bin (writable without a password), else
    /usr/local/bin (which needs sudo)."""
    brew = shutil.which("brew")
    return os.path.dirname(brew) if brew else "/usr/local/bin"


def _docker_action() -> str:
    """What the console can do about a Docker that does not answer. The person installs Docker
    Desktop: its installer and first start ask for their password, which differs from Mac to Mac.
    Once it is there, the console links its command where it looks, or starts it."""
    if sys.platform != "darwin":
        return ""
    if os.path.exists(DOCKER_APP):
        return "start" if shutil.which("docker") else "link"
    return "download"


# Installs run in the background: they take minutes, the person may leave the page meanwhile, and the
# console may restart. The process is detached and writes to a log, so it finishes either way; one
# started before a restart is recognised by its command line, so a second is never started beside it.
_installs: dict[str, dict[str, Any]] = {}
INSTALL_MARKS = {"claude": "install -g @anthropic-ai/claude-code"}


def _install_log(name: str) -> str:
    from aurora.paths import aurora_home
    d = aurora_home() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return str(d / f"install-{name}.log")


def _running_elsewhere(name: str) -> bool:
    return _run(["pgrep", "-f", INSTALL_MARKS[name]])[0] == 0


def _install_state(name: str) -> tuple[str, str]:
    """("running" | "failed" | "", what went wrong)."""
    job = _installs.get(name)
    if job is None:
        return ("running", "") if _running_elsewhere(name) else ("", "")
    code = job["proc"].poll()
    if code is None:
        return "running", ""
    if code != 0:
        try:
            with open(job["log"], encoding="utf-8", errors="replace") as f:
                tail = f.read()[-400:].strip()
        except OSError:
            tail = ""
        return "failed", tail or f"exited with {code}"
    return "", ""


def _start_install(name: str, cmd: list[str], env: dict[str, str] | None = None) -> None:
    if _install_state(name)[0] == "running":
        return
    log = _install_log(name)
    with open(log, "w") as out:
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                                env={**os.environ, **(env or {})}, start_new_session=True)
    _installs[name] = {"proc": proc, "log": log}


def checks() -> list[dict[str, Any]]:
    from aurora.api.routes.campaigns import base_config, live_notifications
    from aurora.auth.secrets import key_for
    from aurora.core.requirements import _docker_ok
    user = current_user()
    cfg = base_config()
    out: list[dict[str, Any]] = []

    key, where = key_for(user)
    if user.is_local:
        fix = "Paste an OpenRouter key below (openrouter.ai → Keys); it is saved in the console’s .env."
    else:
        fix = ("Add yours under the account button → Your API keys." if where == "none" else "")
    out.append({"key": "model_key", "label": "A model key (OpenRouter)", "required": True, "ok": bool(key),
                "detail": {"server": "the server’s key", "own": "your own key", "team": "the team’s key"}.get(where, "none yet")
                if key else "none yet",
                "fix": fix, "action": "key" if user.is_local and not key else ("account" if not key else "")})

    state, error = _install_state("claude")
    claude = shutil.which("claude")
    version = _run([claude, "--version"])[1] if claude else ""
    out.append({"key": "claude", "label": "Claude Code (the agents run in it)", "required": True, "ok": bool(claude),
                "detail": version or "not found",
                "fix": "" if claude else "Install it on the server: npm install -g @anthropic-ai/claude-code (Node.js 18+), then restart the console.",
                "action": "" if claude or not user.is_local else ("installing" if state == "running" else "install"),
                "error": error})

    sandbox = cfg.get("sandbox") or {}
    if sandbox.get("docker", True):
        ok, why = _docker_ok()
        out.append({"key": "docker", "label": "Docker (each run gets its own container)", "required": True, "ok": ok,
                    "detail": "the daemon answers" if ok else why,
                    "fix": "" if ok else "Install Docker and let this user run `docker info`; or set sandbox.docker: false "
                                         "in the server’s config (the agents can then only edit files).",
                    "action": "" if ok or not user.is_local else _docker_action(), "link_dir": _link_dir()})
        image = sandbox.get("image") or "python:3.11-slim"
        if ok:
            have = _run(["docker", "image", "inspect", image], 20)[0] == 0
            out.append({"key": "image", "label": f"The container image ({image})", "required": False, "ok": have,
                        "detail": "pulled" if have else "not pulled yet: the first run pulls it (a few minutes)",
                        "fix": "" if have else f"Pull it now so the first run starts at once: docker pull {image}",
                        "action": "" if have else "pull"})
    else:
        out.append({"key": "docker", "label": "Docker", "required": False, "ok": True,
                    "detail": "off (sandbox.docker: false): the agents can only read and write files", "fix": ""})

    slurm = ((cfg.get("compute") or {}).get("slurm") or {})
    ssh = ((cfg.get("compute") or {}).get("ssh") or {})
    if ssh.get("enabled"):
        code, text = _run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", *(ssh.get("ssh_options") or []),
                           str(ssh.get("host", "")), "nvidia-smi -L | wc -l"], 20)
        n = text.strip().splitlines()[-1] if code == 0 and text.strip() else ""
        out.append({"key": "slurm", "label": f"GPU jobs on {ssh.get('host')} (SSH)", "required": False,
                    "ok": code == 0, "detail": f"reachable, {n} GPU(s)" if code == 0 else f"not reachable: {text[-200:]}",
                    "fix": "" if code == 0 else "This server must reach the machine with `ssh` and a key, without a "
                                                "password (BatchMode); see the guide “GPU jobs”."})
    else:
        out.append({"key": "slurm", "label": "GPU jobs (a SLURM cluster or a GPU machine over SSH)", "required": False,
                    "ok": bool(slurm.get("enabled")), "detail": "SLURM on" if slurm.get("enabled") else "off",
                    "fix": "" if slurm.get("enabled") else "Optional: set compute.slurm or compute.ssh in Settings (see the "
                                                           "guide “GPU jobs”) so agents can run GPU work."})
    try:
        chans = [c for c in live_notifications().channels if c.enabled]
    except Exception:
        chans = []
    out.append({"key": "notify", "label": "Notifications (decisions, finished runs)", "required": False, "ok": bool(chans),
                "detail": f"{len(chans)} channel{'s' if len(chans) != 1 else ''}" if chans else "none",
                "fix": "" if chans else "Optional: Settings → Notifications (WeCom, Feishu, Slack, WeChat via Server酱, a webhook), "
                                        "so you hear when a run needs you.", "action": "" if chans else "settings"})
    oa = bool(os.environ.get("OPENALEX_API_KEY"))
    out.append({"key": "openalex", "label": "OpenAlex key (literature search)", "required": False, "ok": oa,
                "detail": "set" if oa else "not set: a small shared daily budget",
                "fix": "" if oa else "Optional: a free key from openalex.org, as OPENALEX_API_KEY in the server’s .env."})
    return out


@router.get("/setup")
async def get_setup() -> dict[str, Any]:
    from aurora.auth.store import auth_store
    items = await asyncio.to_thread(checks)
    return {"checks": items, "ready": all(c["ok"] for c in items if c["required"]),
            "dismissed": bool(auth_store.get_pref(current_user().user_id, "setup_dismissed")),
            "local": current_user().is_local}


@router.post("/setup/dismiss")
async def dismiss_setup() -> dict[str, Any]:
    from aurora.auth.store import auth_store
    auth_store.set_pref(current_user().user_id, "setup_dismissed", True)
    return {"ok": True}


class KeyBody(BaseModel):
    key: str


@router.put("/setup/key")
async def set_server_key(body: KeyBody) -> dict[str, Any]:
    """The console's own OpenRouter key (a console without accounts only)."""
    if not current_user().is_local:
        raise HTTPException(403, "With accounts, each person adds their own key under the account button")
    from aurora.env import KEY_PATTERN, check_openrouter_key, save_openrouter_key
    key = body.key.strip()
    if not re.fullmatch(KEY_PATTERN, key):
        raise HTTPException(400, "That does not look like an OpenRouter key (sk-or-…)")
    if await asyncio.to_thread(check_openrouter_key, key) is False:
        raise HTTPException(400, "OpenRouter does not accept this key")
    save_openrouter_key(key)                 # offline: kept anyway; the first run will tell
    return await get_setup()


@router.post("/setup/pull")
async def pull_image() -> dict[str, Any]:
    """Pull the container image now (it takes a few minutes the first time)."""
    from aurora.api.routes.campaigns import base_config
    image = ((base_config().get("sandbox") or {}).get("image")) or "python:3.11-slim"
    proc = await asyncio.create_subprocess_exec("docker", "pull", image, stdout=asyncio.subprocess.DEVNULL,
                                                stderr=asyncio.subprocess.PIPE)
    _, err = await proc.communicate()
    if proc.returncode != 0:
        raise HTTPException(400, f"docker pull {image} failed: {err.decode()[-300:]}")
    return await get_setup()


@router.post("/setup/claude")
async def install_claude() -> dict[str, Any]:
    """Install Claude Code with npm on this machine, in the background (a console without accounts only)."""
    if not current_user().is_local:
        raise HTTPException(403, "Claude Code is installed on the server by whoever runs it")
    npm = shutil.which("npm")
    if npm is None:
        raise HTTPException(400, "npm is not on this machine: install Node.js 18 or newer (nodejs.org), then try again")
    _start_install("claude", [npm, "install", "-g", "@anthropic-ai/claude-code"])
    return await get_setup()


@router.post("/setup/docker")
async def start_docker() -> dict[str, Any]:
    """Open Docker Desktop on a Mac where it is installed but not running; the daemon takes a little
    while to answer, so the page checks again."""
    if not current_user().is_local:
        raise HTTPException(403, "Docker runs on the server, started by whoever runs it")
    if sys.platform != "darwin":
        raise HTTPException(400, "Start the Docker daemon on this machine (e.g. sudo systemctl start docker)")
    code, text = await asyncio.to_thread(_run, ["open", "-a", "Docker"])
    if code != 0:
        raise HTTPException(400, "Docker Desktop is not installed: download it from docker.com/products/docker-desktop, "
                                 "open it once, then check again")
    return {"ok": True}


@router.post("/setup/docker/link")
async def link_docker() -> dict[str, Any]:
    """Link the commands of a Docker Desktop installed by hand into Homebrew's bin, where this
    console (and a terminal) finds them, and start Docker."""
    if not current_user().is_local:
        raise HTTPException(403, "Docker is set up on the server by whoever runs it")
    target = _link_dir()
    command = f"ln -s {DOCKER_BIN}/docker {target}/docker"
    if not os.path.isdir(DOCKER_BIN):
        raise HTTPException(400, "Docker Desktop is not in /Applications: install it there, open it once, then check again")
    if not shutil.which("brew"):
        raise HTTPException(400, f"Linking into {target} needs your password: in a terminal, run sudo {command}")
    for name in DOCKER_COMMANDS:
        src, dst = os.path.join(DOCKER_BIN, name), os.path.join(target, name)
        if os.path.exists(src) and not os.path.lexists(dst):
            try:
                os.symlink(src, dst)
            except OSError as exc:
                raise HTTPException(400, f"Could not link {name} ({exc}); in a terminal, run {command}") from None
    await asyncio.to_thread(_run, ["open", "-a", "Docker"])
    return await get_setup()


class DockerPathBody(BaseModel):
    path: str


@router.put("/setup/docker/path")
async def set_docker_path(body: DockerPathBody) -> dict[str, Any]:
    """Where the person says Docker's command is (what `which docker` prints in their terminal), for a
    console that does not find it on its own PATH: kept in ``.env`` and put on PATH from now on."""
    from aurora.env import add_to_path, save_env_value
    if not current_user().is_local:
        raise HTTPException(403, "Docker is set up on the server by whoever runs it")
    path = os.path.expanduser(body.path.strip().strip("'\""))
    if os.path.isdir(path):
        path = os.path.join(path, "docker")
    if not (os.path.isfile(path) and os.access(path, os.X_OK)):
        raise HTTPException(400, f"There is no docker command at {path}. In a terminal, run `which docker` and paste what it prints")
    code, version = await asyncio.to_thread(_run, [path, "--version"])
    if code != 0 or "docker" not in version.lower():
        raise HTTPException(400, f"{path} does not answer as Docker ({version[:120] or 'no output'})")
    directory = os.path.dirname(path)
    save_env_value("AURORA_DOCKER_DIR", directory)
    add_to_path(directory)
    return await get_setup()


DEMO_OBJECTIVE = ("Investigate surface segregation of Zn in Cu(111) and its effect on CO adsorption, as a step towards "
                  "Cu-Zn methanol synthesis catalysts.")


@router.post("/demo")
async def start_demo() -> dict[str, Any]:
    """A run of scripted models and simulated tools: every phase, the auditors' checks, a report, in
    seconds. No key, no Docker, no cost.  It goes into the person's demo project (made the first time)."""
    from fastapi import HTTPException as _HTTPException

    from aurora.api.routes.campaigns import StartExperimentRequest, _ensure_campaign, _start, create_quick_project
    from aurora.auth.guard import can_see
    from aurora.auth.store import auth_store
    me = current_user()
    cid = auth_store.get_pref(me.user_id, "demo_project") or ""
    campaign = None
    if cid and can_see(me, cid):
        try:
            campaign = _ensure_campaign(cid)
        except _HTTPException:
            campaign = None
    if campaign is None:
        project = await create_quick_project("Demo: Zn segregation in Cu(111) (scripted, no cost)", DEMO_OBJECTIVE)
        campaign = _ensure_campaign(project["campaign_id"])
        auth_store.set_pref(me.user_id, "demo_project", campaign.campaign_id)
    req = StartExperimentRequest(instruction=DEMO_OBJECTIVE, name="demo run", mock_mode=True, skip_phases=[],
                                 sandbox_docker=False)
    result = await _start(campaign, req)
    auth_store.set_pref(me.user_id, "onboarding_demo", True)
    return {"campaign_id": campaign.campaign_id, "experiment_id": result["experiment_id"]}
