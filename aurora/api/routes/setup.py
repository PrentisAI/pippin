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

    claude = shutil.which("claude")
    version = _run([claude, "--version"])[1] if claude else ""
    out.append({"key": "claude", "label": "Claude Code (the agents run in it)", "required": True, "ok": bool(claude),
                "detail": version or "not found",
                "fix": "" if claude else "Install it on the server: npm install -g @anthropic-ai/claude-code (Node.js 18+), then restart the console."})

    sandbox = cfg.get("sandbox") or {}
    if sandbox.get("docker", True):
        ok, why = _docker_ok()
        out.append({"key": "docker", "label": "Docker (each run gets its own container)", "required": True, "ok": ok,
                    "detail": "the daemon answers" if ok else why,
                    "fix": "" if ok else "Install Docker and let this user run `docker info`; or set sandbox.docker: false "
                                         "in the server’s config (the agents can then only edit files)."})
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
