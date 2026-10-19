"""Search across the console's projects, runs, conversations, reports and skills; and what the
experiments have cost and how they went (Analytics)."""

from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict
from typing import Any

from fastapi import APIRouter

from aurora.api.routes import campaigns as c

router = APIRouter(prefix="/api", tags=["insights"])


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def _snippet(text: str, q: str, width: int = 160) -> str | None:
    """The part of *text* around the first match of *q* (case-insensitive), or None."""
    i = text.lower().find(q)
    if i < 0:
        return None
    start = max(0, i - width // 3)
    end = min(len(text), i + len(q) + width)
    return ("…" if start else "") + " ".join(text[start:end].split()) + ("…" if end < len(text) else "")


def _search(q: str, per_kind: int) -> dict[str, list[dict[str, Any]]]:
    from aurora.api.routes.skills import get_skill_store
    q = q.lower()
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)

    def add(kind: str, item: dict[str, Any]) -> None:
        if len(out[kind]) < per_kind:
            out[kind].append(item)

    for row in c.store.list_campaigns():
        cid = row["campaign_id"]
        try:
            camp = c._ensure_campaign(cid)
        except Exception:
            continue
        hit = _snippet(f"{camp.name}\n{camp.objective}", q)
        if hit:
            add("projects", {"title": camp.name, "snippet": hit, "campaign_id": cid})
        for m in camp.chat_history:
            hit = _snippet(str(m.get("text", "")), q)
            if hit:
                add("conversations", {"title": f"{camp.name} · project chat", "snippet": hit,
                                      "campaign_id": cid, "role": m.get("role")})
                break
        for run in camp.experiments.values():
            instruction = c._run_instruction(run.run_id)
            hit = _snippet(f"{run.task_name}\n{instruction}", q)
            if hit:
                add("runs", {"title": run.task_name or run.run_id, "snippet": hit, "campaign_id": cid,
                             "run_id": run.run_id, "status": run.status, "project": camp.name})
            report = c._report_path(run)
            if report is not None:
                hit = _snippet(report.read_text(errors="replace"), q)
                if hit:
                    add("reports", {"title": run.task_name or run.run_id, "snippet": hit, "campaign_id": cid,
                                    "run_id": run.run_id, "path": str(report.relative_to(run.campaign_dir)),
                                    "project": camp.name})
            if len(out["conversations"]) < per_kind:
                for text in _run_chat(cid, run.run_id):
                    hit = _snippet(text, q)
                    if hit:
                        add("conversations", {"title": f"{run.task_name or run.run_id} · experiment chat",
                                              "snippet": hit, "campaign_id": cid, "run_id": run.run_id})
                        break
    for sk in get_skill_store().list_all():
        hit = _snippet(f"{sk.name}\n{sk.description}\n{sk.rule}", q)
        if hit:
            add("skills", {"title": sk.name, "snippet": hit, "skill_id": sk.skill_id, "kind": sk.kind})
    return dict(out)


def _run_chat(campaign_id: str, run_id: str) -> list[str]:
    """The messages of a run's conversation, read from its event log (only those lines are parsed)."""
    path = c.store._event_path(campaign_id, run_id)
    texts = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if '"chat_message"' in line or '"frontdesk_response"' in line:
                    try:
                        texts.append(str(json.loads(line).get("data", {}).get("text", "")))
                    except ValueError:
                        continue
    except OSError:
        pass
    return texts


@router.get("/search")
async def search(q: str, limit: int = 8) -> dict[str, Any]:
    """Projects, runs, conversations, reports and skills that mention *q*."""
    q = q.strip()
    if len(q) < 2:
        return {"query": q, "results": {}}
    return {"query": q, "results": await asyncio.to_thread(_search, q, max(1, min(limit, 30)))}


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------

def _analytics(days: int) -> dict[str, Any]:
    since = time.time() - days * 86400
    runs = []
    for row in c.store.list_campaigns():
        try:
            camp = c._ensure_campaign(row["campaign_id"])
        except Exception:
            continue
        for run in camp.experiments.values():
            if (run.started_at or 0) < since:
                continue
            end = run.finished_at or (time.time() if run.status in ("starting", "running", "awaiting_human") else None)
            runs.append({"run_id": run.run_id, "name": run.task_name or run.run_id, "campaign_id": camp.campaign_id,
                         "project": camp.name, "status": run.status, "cost_usd": round(run.cost_usd or 0.0, 4),
                         "started_at": run.started_at, "hours": round((end - run.started_at) / 3600, 2)
                         if end and run.started_at else None})
    by_status: dict[str, int] = defaultdict(int)
    by_project: dict[str, dict[str, Any]] = {}
    by_day: dict[str, float] = defaultdict(float)
    for r in runs:
        by_status[r["status"]] += 1
        p = by_project.setdefault(r["campaign_id"], {"campaign_id": r["campaign_id"], "project": r["project"],
                                                     "cost_usd": 0.0, "runs": 0})
        p["cost_usd"] += r["cost_usd"]
        p["runs"] += 1
        by_day[time.strftime("%Y-%m-%d", time.localtime(r["started_at"]))] += r["cost_usd"]
    finished = [r for r in runs if r["status"] in ("completed", "failed", "stopped")]
    durations = [r["hours"] for r in finished if r["hours"] is not None]
    day_list = [time.strftime("%Y-%m-%d", time.localtime(time.time() - k * 86400)) for k in range(days - 1, -1, -1)]
    return {
        "days": days,
        "total_cost_usd": round(sum(r["cost_usd"] for r in runs), 2),
        "runs": len(runs),
        "by_status": dict(by_status),
        "success_rate": round(by_status.get("completed", 0) / len(finished), 3) if finished else None,
        "mean_hours": round(sum(durations) / len(durations), 2) if durations else None,
        "cost_by_day": [{"day": d, "cost_usd": round(by_day.get(d, 0.0), 2)} for d in day_list],
        "cost_by_project": sorted(({**p, "cost_usd": round(p["cost_usd"], 2)} for p in by_project.values()),
                                  key=lambda p: -p["cost_usd"]),
        "top_runs": sorted(runs, key=lambda r: -r["cost_usd"])[:10],
    }


@router.get("/analytics")
async def analytics(days: int = 30) -> dict[str, Any]:
    """Cost and outcomes of the experiments started in the last *days* days."""
    return await asyncio.to_thread(_analytics, max(1, min(days, 365)))
