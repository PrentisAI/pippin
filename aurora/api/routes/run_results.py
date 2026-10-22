"""A run's results and report, taking them away, and comparing runs.

* ``/experiments/{id}/report``: the report as HTML, with what the run did and its recorded results;
* ``/experiments/{id}/results``: the results and their provenance;
* ``/experiments/{id}/export?what=report|workspace``: a zip; ``/reproduce.sh``: the script alone;
* ``/experiments/{id}/overleaf``: put the report, its LaTeX source and figures into an Overleaf project;
* ``/compare?runs=a,b,c``: runs side by side (setup, outcome, results, conclusions).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel

from aurora.auth.guard import current_user
from aurora.results import report as rp
from aurora.results.ledger import ResultsLedger

router = APIRouter(prefix="/api/campaigns", tags=["results"])
E = "/{campaign_id}/experiments/{exp_id}"


def _run(campaign_id: str, exp_id: str) -> Any:
    from aurora.api.routes.campaigns import _get_run
    return _get_run(campaign_id, exp_id)


def _config(run: Any) -> dict[str, Any]:
    try:
        return yaml.safe_load((run.campaign_dir / "config.yaml").read_text()) or {}
    except (OSError, yaml.YAMLError):
        return {}


def _proposal(run: Any) -> dict[str, Any]:
    """The direction the run took (its latest proposal), and screening's scores."""
    root = run.campaign_dir / "phases"
    out: dict[str, Any] = {}
    props = sorted((p for p in (root / "ideation").glob("round_*/*.json") if not p.name.startswith("meeting_state")),
                   key=lambda p: p.stat().st_mtime) if (root / "ideation").is_dir() else []
    if props:
        try:
            d = json.loads(props[-1].read_text())
            out.update(title=d.get("title", ""), hypothesis=d.get("hypothesis") or d.get("description", ""))
        except (OSError, ValueError):
            pass
    scores = sorted((root / "screening").glob("round_*/score.json"), key=lambda p: p.stat().st_mtime) \
        if (root / "screening").is_dir() else []
    if scores:
        try:
            s = json.loads(scores[-1].read_text())
            out["scores"] = {k: s.get(k) for k in ("novelty", "feasibility", "significance", "composite")}
        except (OSError, ValueError):
            pass
    return out


def _claims(run: Any) -> list[dict[str, Any]]:
    try:
        art = json.loads((run.campaign_dir / "artifacts.json").read_text())
    except (OSError, ValueError):
        return []
    return [{"statement": c.get("statement", ""), "status": c.get("status", ""), "confidence": c.get("confidence")}
            for c in art.get("claims") or []][:30]


def _jobs(run: Any) -> list[Any]:
    from aurora.compute.slurm import saved_jobs
    try:
        return saved_jobs(run.campaign_dir)
    except Exception:
        return []


def _fork(run: Any) -> dict[str, Any] | None:
    try:
        return json.loads((run.campaign_dir / "fork.json").read_text())
    except (OSError, ValueError):
        return None


def overview(run: Any) -> dict[str, Any]:
    """What a run did and found: for its report page and for comparing runs."""
    from aurora.api.routes.campaigns import _run_instruction
    cfg = _config(run)
    models = cfg.get("models") or {}
    used = {k: v for k, v in models.items() if isinstance(v, str) and v}
    if models.get("ideation_seats"):
        used["ideation_seats"] = ", ".join(models["ideation_seats"])
    summary = run.to_summary()
    end = summary.get("finished_at") or time.time()
    ledger = ResultsLedger(run.campaign_dir)
    report = rp.latest_report(run.campaign_dir)
    return {
        "run_id": run.run_id, "name": run.task_name, "status": run.status, "phase": run.phase,
        "instruction": _run_instruction(run.run_id)[:2000], "started_at": summary.get("started_at"),
        "finished_at": summary.get("finished_at"), "wall_seconds": max(0.0, end - (summary.get("started_at") or end)),
        "cost_usd": round(run.cost_usd, 4), "models": used,
        "settings": {"skip_phases": cfg.get("skip_phases") or [], "ideation_mode": (cfg.get("ideation") or {}).get("mode", ""),
                     "autonomy": (cfg.get("human") or {}).get("autonomy", ""),
                     "budget_usd": (cfg.get("budget") or {}).get("max_cost_usd"),
                     "image": (cfg.get("sandbox") or {}).get("image", ""),
                     "cpus": (cfg.get("sandbox") or {}).get("cpus"), "memory_mb": (cfg.get("sandbox") or {}).get("memory_mb")},
        "proposal": _proposal(run), "claims": _claims(run), "results": ledger.all(current=True),
        "results_all": len(ledger.all()), "report": str(report.relative_to(run.campaign_dir)) if report else None,
        "cluster_jobs": len(_jobs(run)), "forked_from": _fork(run),
    }


@router.get(E + "/report")
async def get_report(campaign_id: str, exp_id: str, version: int | None = None) -> dict[str, Any]:
    run = _run(campaign_id, exp_id)
    drafts = sorted((run.campaign_dir / "phases" / "writing").glob("draft_*.md"),
                    key=lambda p: int(re.sub(r"\D", "", p.stem) or 0))
    out = {"overview": overview(run), "versions": [int(re.sub(r"\D", "", p.stem) or 0) for p in drafts],
           "html": "", "title": "", "figures": [], "environment": (run.campaign_dir / "workspace" / ".aurora" /
                                                                   "environment.txt").is_file()}
    if not drafts:
        return out
    path = next((p for p in drafts if version and int(re.sub(r"\D", "", p.stem) or 0) == version), drafts[-1])
    md = path.read_text(errors="replace")
    url = rp.image_url_for(campaign_id, exp_id, path, run.campaign_dir)
    m = re.search(r"^#\s+(.+)$", md, re.M)
    out.update(html=rp.render_html(md, image_url=url), title=m.group(1).strip() if m else run.task_name,
               version=int(re.sub(r"\D", "", path.stem) or 0), path=str(path.relative_to(run.campaign_dir)),
               figures=[{"src": src, "url": url(src), "path": str(f.relative_to(run.campaign_dir))}
                        for src, f in rp.figures_in(md, path.parent, run.campaign_dir)])
    return out


@router.get(E + "/results")
async def get_results(campaign_id: str, exp_id: str) -> dict[str, Any]:
    run = _run(campaign_id, exp_id)
    return {"results": ResultsLedger(run.campaign_dir).all()}


def _bib(campaign_id: str, run: Any) -> str:
    from aurora.literature.bibtex import to_bibtex
    from aurora.literature.library import library
    seen, entries = set(), []
    for u in library.uses(campaign_id, run.run_id):
        if u["paper_id"] and u["paper_id"] not in seen and (e := library.paper(u["paper_id"])):
            seen.add(u["paper_id"])
            entries.append(e)
    return to_bibtex(entries)


def _zip(campaign_id: str, run: Any, what: str) -> bytes:
    return rp.export_zip(run.campaign_dir, what=what, image=(_config(run).get("sandbox") or {}).get("image", ""),
                         run_name=run.task_name or run.run_id, bib=_bib(campaign_id, run), cluster_jobs=_jobs(run))


@router.get(E + "/export")
async def export(campaign_id: str, exp_id: str, what: str = "report") -> Response:
    if what not in ("report", "workspace"):
        raise HTTPException(400, "what: report or workspace")
    run = _run(campaign_id, exp_id)
    data = await asyncio.to_thread(_zip, campaign_id, run, what)
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", run.task_name or run.run_id).strip("-")[:60] or "run"
    return Response(data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{slug}-{what}.zip"'})


@router.get(E + "/reproduce.sh")
async def reproduce(campaign_id: str, exp_id: str) -> PlainTextResponse:
    run = _run(campaign_id, exp_id)
    text = rp.reproduce_script(run.campaign_dir, image=(_config(run).get("sandbox") or {}).get("image", ""),
                               results=ResultsLedger(run.campaign_dir).all(), cluster_jobs=_jobs(run),
                               run_name=run.task_name or run.run_id)
    return PlainTextResponse(text, headers={"Content-Disposition": 'attachment; filename="reproduce.sh"'})


# ---------------------------------------------------------------------------- Overleaf

class OverleafBody(BaseModel):
    url: str = ""                 # the Overleaf project's git URL (remembered per project)
    token: str = ""               # an Overleaf git token (remembered, encrypted, per person)
    folder: str = ""              # where in the project; default aurora/<run>


_OVERLEAF = re.compile(r"^https://git\.overleaf\.com/[0-9a-f]{20,32}$")


def _overleaf_settings(campaign_id: str) -> dict[str, Any]:
    from aurora.auth.secrets import key_status
    from aurora.auth.store import auth_store
    me = current_user()
    return {"url": auth_store.get_pref(me.user_id, f"overleaf_url:{campaign_id}") or "",
            "token": key_status(me.user_id, "overleaf")}


@router.get(E + "/overleaf")
async def overleaf_settings(campaign_id: str, exp_id: str) -> dict[str, Any]:
    _run(campaign_id, exp_id)
    return _overleaf_settings(campaign_id)


def _push(url: str, token: str, folder: str, data: bytes, who: tuple[str, str]) -> str:
    import zipfile
    from io import BytesIO
    auth_url = url.replace("https://", f"https://git:{token}@")
    tmp = Path(tempfile.mkdtemp(prefix="aurora-overleaf-"))
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}

    def git(*args: str) -> str:
        r = subprocess.run(["git", *args], cwd=tmp / "p" if (tmp / "p").exists() else tmp, env=env,
                           capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            msg = (r.stderr or r.stdout).replace(token, "***")
            raise HTTPException(400, f"Overleaf answered: {msg.strip()[-400:] or 'git failed'}")
        return r.stdout
    try:
        git("clone", "--depth", "1", auth_url, "p")
        target = tmp / "p" / folder
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True)
        with zipfile.ZipFile(BytesIO(data)) as z:
            for name in z.namelist():
                if name.startswith(("workspace/", "export/")):
                    continue
                z.extract(name, target)
        git("add", "-A", folder)
        status = git("status", "--porcelain")
        if not status.strip():
            return "Nothing changed in Overleaf: the report there is already this one."
        git("-c", f"user.name={who[0]}", "-c", f"user.email={who[1]}", "commit", "-q", "-m",
            f"AURORA: report of {folder.rsplit('/', 1)[-1]}")
        git("push", "-q", "origin", "HEAD")
        return f"Put into the Overleaf project under {folder}/ (report.tex, figures/, references.bib, results)."
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@router.post(E + "/overleaf")
async def push_overleaf(campaign_id: str, exp_id: str, body: OverleafBody) -> dict[str, Any]:
    """Put the report's LaTeX source, figures, bibliography and results into an Overleaf project
    (Overleaf's git integration: its project URL, and a git token from Account settings)."""
    from aurora.auth.secrets import get_key, put_key
    from aurora.auth.store import auth_store
    run = _run(campaign_id, exp_id)
    me = current_user()
    url = (body.url.strip() or auth_store.get_pref(me.user_id, f"overleaf_url:{campaign_id}") or "").rstrip("/")
    url = re.sub(r"^https://www\.overleaf\.com/project/", "https://git.overleaf.com/", url)
    if not _OVERLEAF.match(url):
        raise HTTPException(400, "Give the Overleaf project's git URL, like https://git.overleaf.com/6ab67eb… "
                                 "(Overleaf → Menu → Git), or the project's address")
    if body.token.strip():
        put_key(me.user_id, "overleaf", body.token.strip())
    token = get_key(me.user_id, "overleaf")
    if not token:
        raise HTTPException(400, "Paste an Overleaf git token (Overleaf → Account settings → Git integration)")
    if rp.latest_report(run.campaign_dir) is None:
        raise HTTPException(400, "This run has no report yet")
    auth_store.set_pref(me.user_id, f"overleaf_url:{campaign_id}", url)
    folder = re.sub(r"[^A-Za-z0-9._/-]+", "-", body.folder.strip().strip("/") or
                    f"aurora/{(run.task_name or run.run_id)[:50]}").strip("-") or "aurora/report"
    data = await asyncio.to_thread(_zip, campaign_id, run, "report")
    who = (me.name if not me.is_local else "AURORA", me.email or "aurora@localhost")
    message = await asyncio.to_thread(_push, url, token, folder, data, who)
    return {"ok": True, "message": message, **_overleaf_settings(campaign_id)}


# ---------------------------------------------------------------------------- forking a run

@router.get(E + "/forkpoints")
async def fork_points(campaign_id: str, exp_id: str) -> dict[str, Any]:
    """Where this run can be forked from, and the ideas its last ideation meeting had (to take another)."""
    from aurora.agents.roundtable.seat import headline
    from aurora.core import forkpoints
    run = _run(campaign_id, exp_id)
    ideas = []
    rounds = sorted((run.campaign_dir / "phases" / "ideation").glob("round_*"), key=lambda p: int(p.name[6:] or 0)) \
        if (run.campaign_dir / "phases" / "ideation").is_dir() else []
    for rd in rounds[-1:]:
        for name in ("meeting_state.finished.json", "meeting_state.json"):
            try:
                state = json.loads((rd / name).read_text())
            except (OSError, ValueError):
                continue
            for c in state.get("contributions", []):
                if c.get("kind") == "presentation" and c.get("content"):
                    ideas.append({"id": f"idea_{c['seat_id']}", "seat": c["seat_id"], "model": c.get("model_id", ""),
                                  "headline": headline(c["content"]), "text": c["content"][:1500]})
            break
    chosen = _proposal(run).get("title", "")
    return {"points": forkpoints.points(run.campaign_dir), "ideas": ideas, "chosen": chosen,
            "running": run.status in ("starting", "running", "awaiting_human")}


class ForkBody(BaseModel):
    point: str                    # a fork point's ID
    name: str = ""
    guidance: str = ""            # what to do differently from there on
    direction: str = ""           # another direction (an idea of the meeting, or one's own), after ideation
    models: dict[str, Any] | None = None
    max_cost_usd: float | None = None


@router.post(E + "/fork")
async def fork_run(campaign_id: str, exp_id: str, body: ForkBody) -> dict[str, Any]:
    """A new run that starts where this one was at the point, with what it had made and spent until then,
    and goes on from there as the researcher says."""
    from aurora.api.routes.campaigns import _ensure_campaign, _saved_request, _start
    from aurora.core import forkpoints
    campaign = _ensure_campaign(campaign_id)
    source = _run(campaign_id, exp_id)
    point = next((p for p in forkpoints.points(source.campaign_dir) if p["id"] == body.point), None)
    if point is None:
        raise HTTPException(404, "No such fork point")
    if not body.guidance.strip() and not body.direction.strip() and not body.models:
        raise HTTPException(400, "Say what the fork should do differently (or choose another direction or model)")
    req = _saved_request(exp_id)
    name = (body.name.strip() or f"{source.task_name or exp_id} (fork)")[:80]
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:30] or "fork"
    run_id = f"{time.strftime('%Y%m%d-%H%M%S')}_{slug}-{os.urandom(2).hex()}"
    from aurora.api.routes.campaigns import ExperimentRunState
    target = ExperimentRunState(run_id, campaign_id, name, "", "supervised").campaign_dir
    record = await asyncio.to_thread(forkpoints.restore_into, source.campaign_dir, body.point, target)
    cp_path = target / "checkpoint.json"
    cp = json.loads(cp_path.read_text()) if cp_path.exists() else {"campaign_id": run_id, "phase": point["phase"],
                                                                   "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S+00:00")}
    cp["campaign_id"] = run_id
    meta = dict(cp.get("metadata") or {})
    if body.guidance.strip():
        meta["fork_guidance"] = body.guidance.strip()[:4000]
    if body.direction.strip():
        meta["fork_direction"] = body.direction.strip()[:4000]
    cp["metadata"] = meta
    cp_path.write_text(json.dumps(cp))
    (target / "fork.json").write_text(json.dumps({"run_id": exp_id, "name": source.task_name, "point": record["label"],
                                                  "point_id": body.point, "inherited_cost_usd": record["cost_usd"],
                                                  "guidance": body.guidance.strip(), "direction": body.direction.strip(),
                                                  "at": time.time()}))
    changes: dict[str, Any] = {"name": name}
    if body.models:
        changes["models"] = {**(req.models or {}), **{k: v for k, v in body.models.items() if v}}
    if "max_cost_usd" in body.model_fields_set:
        changes["max_cost_usd"] = body.max_cost_usd
    req = req.model_copy(update=changes)
    try:
        result = await _start(campaign, req, run_id=run_id, resume=True)
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        raise
    return {**result, "forked_from": {"run_id": exp_id, "point": record["label"], "inherited_cost_usd": record["cost_usd"]}}


# ---------------------------------------------------------------------------- estimating a run

class EstimateBody(BaseModel):
    skip_phases: list[str] = []
    models: dict[str, Any] = {}          # overrides of the console's models (per role)
    model: str = ""                      # models.agent
    ideation_mode: str = ""
    max_parallel_jobs: int | None = None


@router.post("/{campaign_id}/estimate")
async def estimate_run(campaign_id: str, body: EstimateBody) -> dict[str, Any]:
    """What a run with these settings will likely cost and take (before it starts)."""
    from aurora.api.routes.campaigns import _ensure_campaign, base_config, store
    from aurora.auth.guard import visible
    from aurora.core import estimate as est
    _ensure_campaign(campaign_id)
    base = base_config()
    models = {**(base.get("models") or {}), **{k: v for k, v in body.models.items() if v not in ("", None)}}
    if body.model:
        models["agent"] = body.model
    mode = body.ideation_mode or (base.get("ideation") or {}).get("mode", "roundtable")
    parallel = body.max_parallel_jobs or (base.get("agent") or {}).get("max_parallel_jobs", 1) or 1
    runs = []
    for cid in visible(current_user(), [r["campaign_id"] for r in store.list_campaigns()]):
        try:
            runs += list(_ensure_campaign(cid).experiments.values())
        except Exception:
            continue
    history = await asyncio.to_thread(est.history_from_runs, runs)
    prices = await asyncio.to_thread(est.model_prices)
    return est.estimate(models, body.skip_phases, ideation_mode=mode, parallel=int(parallel), history=history,
                        prices=prices)


# ---------------------------------------------------------------------------- comparing runs

@router.get("/{campaign_id}/compare")
async def compare(campaign_id: str, runs: str = "") -> dict[str, Any]:
    """Runs side by side: the setup they differ in, what they cost, their results by name, and what they concluded."""
    from aurora.api.routes.campaigns import _ensure_campaign
    camp = _ensure_campaign(campaign_id)
    recent = sorted(camp.experiments.values(), key=lambda r: -r.started_at)
    # by default the latest finished runs (those with results first), else the latest
    done = [r for r in recent if r.status == "completed"]
    with_results = [r for r in done if ResultsLedger(r.campaign_dir).all()]
    ids = [r for r in runs.split(",") if r] or [r.run_id for r in (with_results or done or recent)][:4]
    views = [overview(camp.experiments[i]) for i in ids if i in camp.experiments]
    names: dict[str, str] = {}
    for v in views:
        for r in v["results"]:
            names.setdefault(r["name"].lower(), r["name"])
    table = []
    for key, label in names.items():
        row = {"name": label, "values": []}
        for v in views:
            r = next((x for x in v["results"] if x["name"].lower() == key), None)
            row["values"].append({"id": r["id"], "value": r["value"], "unit": r.get("unit", ""),
                                  "uncertainty": r.get("uncertainty"), "shown": ResultsLedger.show_value(r)} if r else None)
        table.append(row)
    table.sort(key=lambda row: -sum(1 for x in row["values"] if x))
    return {"runs": views, "results": table,
            "all_runs": [{"run_id": r.run_id, "name": r.task_name, "status": r.status, "started_at": r.started_at}
                         for r in sorted(camp.experiments.values(), key=lambda r: -r.started_at)]}
