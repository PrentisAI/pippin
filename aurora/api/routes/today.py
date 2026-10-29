"""Today: ``/api/today``, the start page of someone who has work going.

What waits for a person, from everywhere at once (a run's decision, a paper retracted or overtaken, a skill to
approve), what changed since yesterday (runs started, finished or stopped; things saved), and the papers most
worth reading. It only gathers: answering a decision, reading a paper or opening a run goes through the routes
that own them.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter

from aurora.auth.guard import current_user

router = APIRouter(prefix="/api/today", tags=["today"])

SINCE = 24 * 3600               # "since yesterday"
ALERT_DAYS = 7


async def _decisions(projects: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The decisions runs wait for, and what the runs did since yesterday."""
    from aurora.api.routes.campaigns import get_campaign
    needs, changed = [], []
    since = time.time() - SINCE
    for p in projects:
        if not (p.get("needs_attention") or p.get("active_experiments") or p.get("updated_at", 0) >= since):
            continue
        try:
            c = await get_campaign(p["campaign_id"])
        except Exception:
            continue
        for e in c.get("experiments", []):
            run = e.get("task_name") or e["run_id"]
            d = e.get("pending_decision")
            if d:
                g = d.get("detail") or {}
                needs.append({"kind": "decision", "id": d["decision_id"], "campaign_id": p["campaign_id"], "run_id": e["run_id"],
                              "project": p["name"], "run": run, "source": d.get("source", ""),
                              "title": g.get("title") or d["context"].split("\n")[0], "detail": g.get("detail", ""),
                              "options": d.get("options", []), "at": d.get("created_at") or e.get("started_at") or 0})
            if (e.get("finished_at") or 0) >= since:
                status = e.get("status", "")
                verb = "finished" if status == "completed" else "stopped" if status in ("stopped", "cancelled") else "failed" \
                    if status in ("failed", "error") else "ended"
                changed.append({"kind": "run", "at": e["finished_at"], "campaign_id": p["campaign_id"], "run_id": e["run_id"],
                                "text": f"{p['name']} · {run} {verb}" + (f", ${e['cost_usd']:.2f}" if e.get("cost_usd") else ""),
                                "tone": "ok" if verb == "finished" else "bad"})
            elif (e.get("started_at") or 0) >= since:
                changed.append({"kind": "run", "at": e["started_at"], "campaign_id": p["campaign_id"], "run_id": e["run_id"],
                                "text": f"{p['name']} · {run} started", "tone": ""})
    return needs, changed


def _alerts(uid: str) -> list[dict[str, Any]]:
    """What the notebook flagged and has not been seen: a retraction, a new paper that overlaps your aim."""
    from aurora.memory.store import mem_store
    rows = mem_store.alerts(uid, since=time.time() - ALERT_DAYS * 86400, unseen=True)
    return [{"kind": "alert", "id": a["alert_id"], "alert": a["kind"], "title": a["title"], "detail": a["body"],
             "ref": a.get("ref") or {}, "at": a["created_at"]} for a in rows if a["kind"] != "decision"]


def _skills() -> list[dict[str, Any]]:
    """Skills waiting for a team admin before they reach other projects (only an admin is asked)."""
    user = current_user()
    if not user.is_admin or user.is_local:
        return []
    from aurora.api.routes.skills import get_skill_store
    return [{"kind": "skill", "id": s.skill_id, "title": f"Let the skill “{s.name}” reach other projects?",
             "detail": s.description, "at": s.updated_at} for s in get_skill_store().pending()]


def _saved(uid: str) -> list[dict[str, Any]]:
    from aurora.memory.store import mem_store
    since = time.time() - SINCE
    rows = [s for s in mem_store.sources(uid, 60) if s["created_at"] >= since and s["state"] == "done"]
    if not rows:
        return []
    names = [s.get("title") or s.get("url") or "" for s in rows[:2]]
    more = len(rows) - len(names)
    return [{"kind": "memory", "at": rows[0]["created_at"], "tone": "",
             "text": f"Saved into your notebook: {'; '.join(n[:80] for n in names if n)}" + (f", and {more} more" if more > 0 else "")}]


def _reading(uid: str, limit: int = 3) -> list[dict[str, Any]]:
    """The feed's best fits for your projects, as the Library ranks them."""
    try:
        from aurora.api.routes.feed import _projects, item_view
        from aurora.feed import service as svc
        from aurora.feed.store import feed_store
        projects = _projects()
        rows = [r for r in feed_store.items(uid, time.time() - svc.SHOW_DAYS * 86400) if r["ranked"] and not svc.is_old(r)]
        ranks = feed_store.ranks([r["item_id"] for r in rows])
        items = [item_view(r, ranks.get(r["item_id"], []), projects, {}) for r in rows]
    except Exception:
        return []
    items = [i for i in items if i["rel"] >= 3]
    items.sort(key=lambda x: (-x["rel"], -(x["published_at"] or x["fetched_at"])))
    return [{"item_id": i["item_id"], "title": i["title"], "url": i["url"], "why": i["why"], "project": i["project"],
             "label": i["label"], "source": i["source"], "is_paper": i["is_paper"]} for i in items[:limit]]


@router.get("")
async def today() -> dict[str, Any]:
    from aurora.api.routes.campaigns import list_campaigns
    user = current_user()
    projects = [p for p in await list_campaigns() if not p.get("example")]
    decisions, runs = await _decisions(projects)
    needs = sorted(decisions + _alerts(user.user_id) + _skills(), key=lambda n: n["at"])
    changed = sorted(runs + _saved(user.user_id), key=lambda c: -c["at"])[:8]
    running = sum(p.get("active_experiments", 0) for p in projects)
    return {"name": "" if user.is_local else user.name.split()[0] if user.name else "", "needs": needs, "changed": changed,
            "reading": _reading(user.user_id), "projects": len(projects), "running": running}
