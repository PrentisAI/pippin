"""When Pippin interrupts: only for what you would regret missing.

Two kinds of thing interrupt, everything else waits for the daily digest:

* **retraction**: a paper you rely on (in your notebook, your Library, or a manuscript whose references you checked)
  has been retracted. Checked once a day against OpenAlex (``is_retracted``) and Crossref's retraction notices
  (which include the Retraction Watch data).
* **overlap**: a new paper that comes in (saved, or from what you follow) does what you set out to do: it overlaps
  a research question you settled with the Mentor, and the plan you made for it. A model confirms the overlap.

An interruption goes to the desktop app and the browser extension (they poll for it) and to your notification
channels that take ``memory_alert``. At most ``DAILY`` overlaps interrupt per day; quiet hours hold them for the
digest. A retraction always interrupts. Saying an overlap was not worth it moves overlaps to the digest from then on.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from aurora.memory import ingest
from aurora.memory.store import mem_store

logger = logging.getLogger(__name__)

DAILY = 2                 # overlaps that may interrupt per day
OVERLAP = 0.75            # relevance to a settled question from which a model is asked whether it overlaps
CHECK_EVERY = 86400       # seconds between retraction checks of one paper


def demoted(owner: str, kind: str) -> bool:
    from aurora.auth.store import auth_store
    return kind in (auth_store.get_pref(owner, "memory_alert_demoted", []) or [])


ALWAYS = ("retraction", "decision")     # interrupt whatever the quota and the quiet hours


def raise_alert(owner: str, kind: str, key: str, title: str, body: str, ref: dict[str, Any] | None = None,
                *, deliver_to_channels: bool = True) -> dict[str, Any] | None:
    """Record an alert (once per thing) and decide whether it interrupts now or waits for the digest."""
    tier = "now"
    if kind not in ALWAYS:
        today = [a for a in mem_store.alerts(owner, since=time.time() - 86400) if a["tier"] == "now" and a["kind"] not in ALWAYS]
        if demoted(owner, kind) or len(today) >= DAILY or _quiet(owner):
            tier = "digest"
    a = mem_store.add_alert(owner, kind, tier, key, title, body, ref)
    if a is not None and tier == "now" and deliver_to_channels:
        _deliver_soon(owner, a)
    return a


def decision_opened(owner: str, gate_id: str, run_name: str, title: str, url: str) -> None:
    """A running experiment waits for your decision: the desktop orb's dot and the extension's notification too (the
    notification channels already have it, as decision_needed)."""
    raise_alert(owner, "decision", gate_id, f"Decision needed: {run_name}", title, {"url": url},
                deliver_to_channels=False)


def decision_closed(owner: str, gate_id: str) -> None:
    for a in mem_store.alerts(owner, since=time.time() - 30 * 86400):
        if a["kind"] == "decision" and a["key"] == gate_id and not a["seen_at"]:
            mem_store.update_alert(a["alert_id"], seen_at=time.time())


def _quiet(owner: str) -> bool:
    try:
        from aurora.api.routes.campaigns import live_notifications
        from aurora.notify import in_quiet_hours
        return in_quiet_hours(live_notifications(owner))
    except Exception:
        return False


_tasks: set[Any] = set()


def _deliver_soon(owner: str, alert: dict[str, Any]) -> None:
    import asyncio
    try:
        task = asyncio.get_running_loop().create_task(deliver(owner, alert))
    except RuntimeError:
        return
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def deliver(owner: str, alert: dict[str, Any]) -> str:
    """Send an interrupting alert to the channels that take it; returns where it went."""
    try:
        from aurora.api.routes.campaigns import console_url, live_notifications
        from aurora.notify import deliver as send
        channels = [c for c in live_notifications(owner).channels if c.enabled and "memory_alert" in c.events]
    except Exception:
        channels = []
    sent = []
    for c in channels:
        try:
            err = await send(c, "memory_alert", alert["title"], alert["body"], url=f"{console_url()}/" if console_url() else "")
            sent.append(c.name or c.type if not err else f"{c.name or c.type}: {err}")
        except Exception as exc:
            sent.append(f"{c.name or c.type}: {exc}")
    mem_store.update_alert(alert["alert_id"], sent="; ".join(sent) or "the desktop app and the extension")
    return "; ".join(sent)


def verdict(owner: str, alert_id: str, worth: bool) -> dict[str, Any]:
    """The person says whether an interruption was worth it; not worth it moves its kind to the digest."""
    from aurora.auth.store import auth_store
    a = mem_store.alert(alert_id)
    if a is None or a["owner"] != owner:
        raise ValueError("no such alert")
    mem_store.update_alert(alert_id, verdict="worth" if worth else "not_worth", seen_at=a["seen_at"] or time.time())
    if not worth and a["kind"] not in ALWAYS:
        kinds = set(auth_store.get_pref(owner, "memory_alert_demoted", []) or [])
        kinds.add(a["kind"])
        auth_store.set_pref(owner, "memory_alert_demoted", sorted(kinds))
    return mem_store.alert(alert_id)  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Overlap with what you set out to do


async def check_overlap(access: Any, models: dict[str, str], owner: str, source_id: str) -> dict[str, Any] | None:
    """A new paper against what you set out to do: the research questions settled with the Mentor (and their plans),
    and the contributions of the manuscripts whose references you checked."""
    from aurora.memory import questions as qs
    from aurora.review import llm
    src = mem_store.source(source_id)
    if src is None or not src["paper_id"] or src["state"] != "done":
        return None
    targets = [{"kind": "question", "id": q["question_id"], "label": q["label"], "text": q["text"], "q": q}
               for q in qs.current(owner) if q["origin"] == "mentor"]
    targets += [{"kind": "manuscript", "id": m["audit_id"], "label": m["title"], "text": f"{m['title']}\n{m['abstract']}"}
                for m in manuscripts(owner)]
    if not targets:
        return None
    text = mem_store.snapshot(source_id)[:6000]
    vecs = await ingest.embed(access, models["embedding"], [f"{src['title']}\n{text[:3000]}"] + [t["text"][:3000] for t in targets])
    rel, t = max(((ingest.relevance(ingest.cosine(vecs[0], v)), t) for v, t in zip(vecs[1:], targets, strict=False)),
                 key=lambda x: x[0], default=(0.0, None))
    if t is None or rel < OVERLAP:
        return None
    if t["kind"] == "question":
        what = f"research question and plan:\nQuestion: {t['text']}\n{_plan_of(t['q'])}"
        title = f"A new paper overlaps your question {t['label']}"
    else:
        what = f"manuscript (its title and abstract, the contribution it claims):\n{t['text']}"
        title = f"A new paper overlaps your manuscript “{t['label'][:80]}”"
    out = await llm.ask(access.with_model(models["memory"]), "memory_overlap", f"""A researcher has this \
{what}

A new paper just came in:
{src['title']}
{text}

Does this paper do what the researcher set out to do (the same question answered in largely the same way, or the \
same contribution), so that their contribution is at risk? "high": it does; "partial": it covers part of it, or answers \
it differently; "no": it is only related. One sentence why, naming what overlaps.

JSON: {{"overlap": "high|partial|no", "why": "..."}}""", max_tokens=3000)
    if out.get("overlap") != "high":
        return None
    why = llm.unlabel(out.get("why", ""))
    ref = {"source_id": source_id, "paper_id": src["paper_id"], t["kind"] + "_id": t["id"]}
    return raise_alert(owner, "overlap", src["paper_id"], title, f"{src['title']}\n{why}", ref)


def manuscripts(owner: str) -> list[dict[str, Any]]:
    """The manuscripts whose references you checked: their title and abstract (what they claim to contribute)."""
    from aurora.auth.store import auth_store
    return auth_store.get_pref(owner, "memory_manuscripts", []) or []


def remember_manuscript(owner: str, audit_id: str, title: str, abstract: str) -> None:
    from aurora.auth.store import auth_store
    if len(abstract) < 100:
        return
    rows = [m for m in manuscripts(owner) if m["title"] != title]
    rows.append({"audit_id": audit_id, "title": title[:200] or "Your manuscript", "abstract": abstract[:3000]})
    auth_store.set_pref(owner, "memory_manuscripts", rows[-5:])


def _plan_of(question: dict[str, Any]) -> str:
    """The plan made with the Mentor for a settled question (method, evidence, contribution), as text."""
    from aurora.brainstorm.store import bs_store
    ref = question.get("ref") or ""
    if not ref:
        return ""
    lines = [f"- ({i['section']}) {i['text']}" for i in bs_store.insights(ref) if i["section"] != "question"]
    return "Their plan:\n" + "\n".join(lines[:12]) if lines else ""


# ---------------------------------------------------------------------------
# Retractions


def relied_on(owner: str) -> dict[str, dict[str, Any]]:
    """DOIs of the papers the person relies on: {doi: {title, paper_id, where}}."""
    from aurora.literature.library import library
    out: dict[str, dict[str, Any]] = {}

    def add(paper_id: str, where: str) -> None:
        e = library.paper(paper_id)
        if e and e["paper"].doi:
            row = out.setdefault(e["paper"].doi.lower(), {"title": e["paper"].title, "paper_id": paper_id, "where": []})
            if where not in row["where"]:
                row["where"].append(where)
    for s in mem_store.sources(owner, 2000):
        if s["paper_id"] and s["state"] == "done":
            add(s["paper_id"], "your notebook")
    for item in library.items("", owner=owner):
        add(item["paper_id"], "your Library")
    for doi, row in (_audited(owner) or {}).items():
        out.setdefault(doi, {"title": row["title"], "paper_id": "", "where": []})["where"].append(row["where"])
    return out


def _audited(owner: str) -> dict[str, dict[str, Any]]:
    """References of the manuscripts whose references the person checked (they rely on those too)."""
    from aurora.auth.store import auth_store
    return auth_store.get_pref(owner, "memory_audited_dois", {}) or {}


async def is_retracted(dois: list[str]) -> dict[str, str]:
    """{doi: notice} of the retracted ones among ``dois`` (OpenAlex, then Crossref's notices)."""
    import httpx
    found: dict[str, str] = {}
    async with httpx.AsyncClient(timeout=30, headers={"User-Agent": "Pippin (mailto:aurora@prentis.ai)"}) as c:
        for i in range(0, len(dois), 40):
            chunk = dois[i:i + 40]
            try:
                r = await c.get("https://api.openalex.org/works", params={
                    "filter": f"doi:{'|'.join(chunk)},is_retracted:true", "per-page": 50, "select": "doi,title"})
                for w in (r.json().get("results") or []) if r.status_code == 200 else []:
                    doi = str(w.get("doi") or "").lower().removeprefix("https://doi.org/")
                    if doi:
                        found[doi] = "OpenAlex marks it retracted"
            except Exception:
                logger.info("OpenAlex retraction check failed", exc_info=True)
        for doi in dois:
            if doi in found:
                continue
            try:
                r = await c.get("https://api.crossref.org/works", params={"filter": f"updates:{doi}", "rows": 5})
                for n in (r.json().get("message") or {}).get("items") or [] if r.status_code == 200 else []:
                    for u in n.get("update-to") or []:
                        if str(u.get("DOI", "")).lower() == doi and "retract" in str(u.get("type", "")).lower():
                            found[doi] = f"Retraction notice {n.get('DOI', '')}"
            except Exception:
                logger.info("Crossref retraction check failed for %s", doi, exc_info=True)
    return found


async def check_retractions(owner: str, *, force: bool = False) -> list[dict[str, Any]]:
    """Check the papers the person relies on (each at most once a day); alert about new retractions."""
    papers = relied_on(owner)
    due = [d for d in papers if force or (r := mem_store.retraction(d)) is None or time.time() - r["checked_at"] > CHECK_EVERY]
    if not due:
        return []
    found = await is_retracted(due)
    raised = []
    for doi in due:
        before = mem_store.retraction(doi)
        mem_store.set_retraction(doi, doi in found, found.get(doi, ""))
        if doi in found and not (before and before["retracted"]):
            p = papers[doi]
            a = raise_alert(owner, "retraction", doi, "A paper you rely on has been retracted",
                            f"{p['title']} (doi:{doi}). In {', '.join(p['where'])}. {found[doi]}.",
                            {"doi": doi, "paper_id": p["paper_id"], "where": p["where"]})
            if a:
                raised.append(a)
    return raised
