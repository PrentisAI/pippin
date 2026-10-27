"""What surfaces now: the person's idea units ranked against what they are doing, in three layers.

* **surface**: bears directly on the context and tells something new (at most three; conflicts first);
* **absorbed**: bears on it, but its point is already part of what the question has gathered (at most five);
* **sunk**: does not bear on it now, or the person let it sink. Nothing is deleted; another context may raise it.

The context is what an entry point reports (the page, the paper and its page, the selection, the note being
written), a research question, or a paper. Units are ranked coarsely by embedding, then the top twenty by a model
that says, for each, how much it bears, whether it tells something new, how it stands to the context (challenges,
adds, supports, background, a tool) and why it is at that depth.

Also here: asking the person a question from what surfaced (instead of a summary), and the weekly report.
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Any

from aurora.memory import ingest
from aurora.memory import questions as qs
from aurora.memory.store import mem_store

logger = logging.getLogger(__name__)

SURFACE, ABSORBED = 3, 5
STANCES = ("challenges", "adds", "supports", "background", "tool")
CONTEXT_TTL = 6 * 3600
_contexts: dict[str, dict[str, Any]] = {}            # owner -> the latest context an entry point reported
_cache: dict[tuple[str, str, str], dict[str, Any]] = {}
_ctx_vecs: dict[str, list[float]] = {}


def report_context(owner: str, ctx: dict[str, Any]) -> dict[str, Any]:
    """An entry point says what the person is doing now (kept in memory only, never stored)."""
    keep = {k: str(ctx.get(k) or "")[: (6000 if k == "text" else 600)] for k in
            ("app", "title", "url", "paper_id", "page", "selection", "text", "via")}
    keep["at"] = time.time()
    _contexts[owner] = keep
    return keep


def current_context(owner: str) -> dict[str, Any] | None:
    c = _contexts.get(owner)
    return c if c and time.time() - c["at"] < CONTEXT_TTL else None


def language(owner: str) -> str:
    from aurora.auth.store import auth_store
    lang = auth_store.get_pref(owner, "memory_language", "auto")
    return {"zh": "Chinese (Simplified)", "en": "English"}.get(lang, "the language of the context")


def resolve(owner: str, key: str) -> dict[str, Any]:
    """A context by key: ``current``, ``q:<question>`` or ``paper:<paper>``."""
    if key.startswith("q:"):
        q = mem_store.question(key[2:])
        if q is None or q["owner"] != owner:
            raise ValueError("no such question")
        label = next((x["label"] for x in qs.current(owner) if x["question_id"] == q["question_id"]), "Q")
        return {"key": key, "kind": "question", "label": q["text"], "chip": label, "sub": "Research question",
                "text": q["text"], "question_id": q["question_id"]}
    if key.startswith("paper:"):
        from aurora.literature.library import library
        e = library.paper(key[6:])
        if e is None:
            raise ValueError("no such paper")
        return {"key": key, "kind": "paper", "label": e["paper"].title, "chip": "Paper", "sub": e["paper"].byline(),
                "text": f"{e['paper'].title}\n\n{e['paper'].abstract or ''}"}
    c = current_context(owner)
    if c is None:
        raise ValueError("nothing is open: no entry point has said what you are reading")
    text = "\n\n".join(x for x in (c["title"], c["selection"] and f"Selected: {c['selection']}", c["text"][:3000]) if x)
    return {"key": "current", "kind": "reading", "label": c["title"] or c["url"] or "What you are reading",
            "chip": "Reading", "sub": f"Recognised · {c['app'] or 'the current window'}" + (f" · page {c['page']}" if c["page"] else ""),
            "text": text, "selection": c["selection"]}


async def _ctx_vec(access: Any, model: str, text: str) -> list[float]:
    k = hashlib.sha1(f"{model}:{text}".encode()).hexdigest()
    if k not in _ctx_vecs:
        _ctx_vecs[k] = (await ingest.embed(access, model, [text]))[0]
        if len(_ctx_vecs) > 500:
            _ctx_vecs.pop(next(iter(_ctx_vecs)))
    return _ctx_vecs[k]


async def rank(access: Any, models: dict[str, str], owner: str, ctx: dict[str, Any]) -> dict[str, Any]:
    """The three layers for a context."""
    key = (owner, hashlib.sha1(ctx["text"].encode()).hexdigest(), mem_store.version(owner))
    if key in _cache and time.time() - _cache[key]["at"] < 900:
        return _cache[key]["out"]
    units = mem_store.units(owner)
    live = [u for u in units if not u["sunk"]]
    vec = await _ctx_vec(access, models["embedding"], ctx["text"])
    uvecs = await ingest.unit_vectors(access, models["embedding"], live)
    coarse = sorted(((ingest.relevance(ingest.cosine(vec, uvecs[u["unit_id"]])), u) for u in live if u["unit_id"] in uvecs),
                    key=lambda x: -x[0])
    top = [(r, u) for r, u in coarse[:20] if r >= 0.25]
    fine: dict[str, dict[str, Any]] = {}
    if top:
        try:
            fine = await _judge(access.with_model(models["memory"]), ctx, [u for _, u in top], language(owner))
        except Exception:
            logger.info("ranking the memory for a context failed; using the embedding ranking", exc_info=True)
    scored = []
    for r, u in top:
        f = fine.get(u["unit_id"]) or {}
        scored.append({**u, "relevance": float(f.get("relevance", r)), "new": bool(f.get("new", True)),
                       "stance": f.get("stance") if f.get("stance") in STANCES else "background",
                       "why_here": f.get("why", "")})
    links = mem_store.links([u["unit_id"] for u in scored])
    clash: dict[str, str] = {}
    ids = {u["unit_id"] for u in scored}
    for lk in links:
        if lk["rel"] == "contradicts" and lk["a"] in ids and lk["b"] in ids:
            clash.setdefault(lk["a"], lk["b"])
            clash.setdefault(lk["b"], lk["a"])
    scored.sort(key=lambda u: (-(u["relevance"] >= 0.7 and u["new"]), -(u["unit_id"] in clash), -u["relevance"]))
    surface, absorbed, placed = [], [], set()
    for u in scored:
        if u["relevance"] >= 0.7 and u["new"] and len(surface) < SURFACE:
            surface.append(u)
        elif u["relevance"] >= 0.3 and len(absorbed) < ABSORBED:
            absorbed.append(u)
        else:
            continue
        placed.add(u["unit_id"])
    sunk = [u for u in units if u["unit_id"] not in placed]
    out = {"surface": [_item(u, "surface", ctx, clash) for u in surface],
           "absorbed": [_item(u, "absorbed", ctx, clash) for u in absorbed],
           "sunk": [_item(u, "sunk", ctx, clash) for u in sunk[:60]], "n_sunk": len(sunk)}
    on = {x["unit_id"] for x in out["surface"]}
    out["conflicts"] = sum(1 for a, b in clash.items() if a < b and (a in on or b in on))
    _cache[key] = {"at": time.time(), "out": out}
    if len(_cache) > 200:
        _cache.pop(next(iter(_cache)))
    return out


async def _judge(access: Any, ctx: dict[str, Any], units: list[dict[str, Any]], lang: str) -> dict[str, dict[str, Any]]:
    from aurora.review import llm
    lines = "\n".join(f"[{u['unit_id']}] {u['claim']} — {u['gist'][:300]}" for u in units)
    out = await llm.ask(access, "memory_surface", f"""A researcher is doing this now:
{ctx['sub']}: {ctx['label']}
{ctx['text'][:4000]}

These are claims from what they saved before. For each, judge it against what they are doing NOW:
- "relevance": 0 to 1, how directly it bears on it (1: about the very claim or question in front of them);
- "new": true if it tells them something the context itself does not already say;
- "stance": challenges (it argues against or qualifies what they are reading), adds (new information on the same \
point), supports, background, or tool;
- "why": one sentence in {lang}, why it deserves this depth now (or why not), concrete.

{lines}

JSON: {{"units": [{{"id": "u_...", "relevance": 0.8, "new": true, "stance": "challenges", "why": "..."}}]}}""",
                        want="units", max_tokens=3000)
    got = {}
    for x in out.get("units") or []:
        if isinstance(x, dict) and x.get("id"):
            try:
                rel = max(0.0, min(1.0, float(x.get("relevance", 0))))
            except (TypeError, ValueError):
                continue
            got[str(x["id"])] = {"relevance": rel, "new": bool(x.get("new", True)), "stance": x.get("stance"),
                                 "why": llm.unlabel(x.get("why", ""))}
    return got


def _item(u: dict[str, Any], layer: str, ctx: dict[str, Any], clash: dict[str, str]) -> dict[str, Any]:
    ev = mem_store.evidence(u["unit_id"])
    platforms = list(dict.fromkeys(e["platform"] for e in ev))
    other = mem_store.unit(clash[u["unit_id"]]) if u["unit_id"] in clash else None
    marks = mem_store.marks([u["unit_id"]])
    moved = False
    for m in marks:
        moved = True if m["kind"] == "moved" else False if m["kind"] == "unmoved" else moved
    if u.get("sunk"):
        why = "You let it sink. It stays down here and will not surface on its own; search still finds it."
    elif layer == "surface":
        why = u.get("why_here") or "Bears directly on what you are doing now, and tells you something new."
    elif layer == "absorbed":
        where = ctx["chip"] if ctx["kind"] == "question" else "what you are reading"
        why = u.get("why_here") or f"Read for you; its point is part of what {where} has gathered. You need not read it."
    else:
        why = "Unrelated to this context, so it sits deep. It is not deleted; another context may raise it."
    return {"unit_id": u["unit_id"], "claim": u["claim"], "gist": u["gist"], "kind": u["kind"], "layer": layer,
            "stance": u.get("stance", "background"), "why": why, "relevance": round(u.get("relevance", 0.0), 3),
            "sources": len({e["source_id"] for e in ev}), "platforms": platforms, "sunk": bool(u.get("sunk")),
            "clash_with": {"unit_id": other["unit_id"], "claim": other["claim"]} if other else None, "moved": moved,
            "answers": [{"question": a["question"], "answer": a["answer"], "at": a["answered_at"]}
                        for a in mem_store.asks(u["owner"]) if a["answer"] and u["unit_id"] in a["basis"]][-2:]}


# ---------------------------------------------------------------------------
# Asking instead of summarising


async def ask(access: Any, models: dict[str, str], owner: str, ctx: dict[str, Any]) -> dict[str, Any] | None:
    """One question to the researcher, grounded in what surfaced for the context (a conflict if there is one)."""
    from aurora.review import llm
    layers = await rank(access, models, owner, ctx)
    pool = layers["surface"] + layers["absorbed"]
    if not pool:
        return None
    first = next((u for u in pool if u["clash_with"]), None)
    basis = [first["unit_id"], first["clash_with"]["unit_id"]] if first else [u["unit_id"] for u in pool[:2]]
    units = [mem_store.unit(b) for b in basis]
    lines = "\n".join(f"- {u['claim']} ({u['gist'][:300]})" for u in units if u)
    earlier = [a for a in mem_store.asks(owner) if a["answer"] and set(a["basis"]) & set(basis)]
    past = "\n".join(f"- Asked: {a['question']} They answered: {a['answer']}" for a in earlier[-2:])
    out = await llm.ask(access.with_model(models["memory"]), "memory_ask", f"""A researcher is doing this now:
{ctx['sub']}: {ctx['label']}
{ctx['text'][:2500]}

From what they saved, these bear on it{' and contradict each other' if first else ''}:
{lines}
{('What they said before about these:' + chr(10) + past) if past else ''}

Do not summarise. Ask them ONE question, in {lang_of(owner)}, that makes them take a position or connect this to \
their own work: which side they lean to and why, what in their own setup decides it, what would change their mind. \
Name the claims it rests on in a few words. Two or three sentences at most.

JSON: {{"question": "..."}}""", max_tokens=1200)
    q = llm.unlabel(out.get("question", ""))
    if not q:
        return None
    a = mem_store.add_ask(owner, ctx["key"], q, basis)
    return {**a, "basis_units": [{"unit_id": u["unit_id"], "claim": u["claim"]} for u in units if u]}


def lang_of(owner: str) -> str:
    return language(owner)


def answer(owner: str, ask_id: str, text: str) -> dict[str, Any]:
    a = mem_store.ask(ask_id)
    if a is None or a["owner"] != owner:
        raise ValueError("no such question")
    mem_store.answer_ask(ask_id, text)
    for uid in a["basis"]:
        mem_store.mark(uid, owner, "answer", {"ask_id": ask_id})
    return mem_store.ask(ask_id)  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# A question's synthesis (what "absorbed" means) and the weekly report


async def synthesis(access: Any, models: dict[str, str], owner: str, question_id: str) -> dict[str, Any]:
    from aurora.review import llm
    q = mem_store.question(question_id)
    if q is None or q["owner"] != owner:
        raise ValueError("no such question")
    rows = [b for b in mem_store.bears(question_id=question_id) if b["relevance"] >= 0.3][:20]
    units = [u for b in rows if (u := mem_store.unit(b["unit_id"])) and not u["sunk"]]
    if not units:
        return {"question": q["text"], "text": "", "units": []}
    ref = {u["unit_id"]: f"U{i + 1}" for i, u in enumerate(units)}
    lines = "\n".join(f"[{ref[u['unit_id']]}] {u['claim']} — {u['gist'][:300]}" for u in units)
    out = await llm.ask(access.with_model(models["memory"]), "memory_synthesis", f"""The researcher's question: \
{q['text']}

What their memory holds on it:
{lines}

Write, in {lang_of(owner)}, what this adds up to for their question: where the claims agree, where they conflict and \
why, what is still open. Integrate, do not list one claim after another. Cite claims only as [U1], [U2]. At most \
two short paragraphs.

JSON: {{"text": "..."}}""", max_tokens=2000)
    return {"question": q["text"], "text": llm.unlabel(out.get("text", "")),
            "units": [{"ref": ref[u["unit_id"]], "unit_id": u["unit_id"], "claim": u["claim"]} for u in units]}


def report(owner: str, days: int = 7) -> dict[str, Any]:
    """The week, by question: what was taken in, new conflicts, what sank, changed views and answers."""
    since = time.time() - days * 86400
    questions = qs.current(owner)
    units = mem_store.units(owner)
    new_units = [u for u in units if u["created_at"] >= since]
    sources = [s for s in mem_store.sources(owner, 1000) if s["created_at"] >= since and s["state"] == "done"]
    links = [lk for lk in mem_store.links(owner=owner) if lk["created_at"] >= since and lk["rel"] == "contradicts"]
    marks = mem_store.marks(owner=owner, since=since)
    asks = [a for a in mem_store.asks(owner, since) if a["answer"]]
    claim = {u["unit_id"]: u["claim"] for u in units}
    by_q = []
    for q in questions:
        bearing = {b["unit_id"] for b in mem_store.bears(question_id=q["question_id"]) if b["relevance"] >= 0.5}
        by_q.append({"question_id": q["question_id"], "label": q["label"], "text": q["text"],
                     "new_units": [{"unit_id": u["unit_id"], "claim": u["claim"]} for u in new_units
                                   if u["unit_id"] in bearing],
                     "conflicts": [{"a": claim.get(lk["a"], ""), "b": claim.get(lk["b"], "")} for lk in links
                                   if lk["a"] in bearing or lk["b"] in bearing]})
    old_sunk = [u for u in units if u["sunk"] and u["updated_at"] < since]
    return {"days": days, "sources": len(sources), "merged": sum(len(s["result"].get("merged") or []) for s in sources),
            "new_units": len(new_units), "questions": by_q,
            "conflicts": [{"a": claim.get(lk["a"], ""), "b": claim.get(lk["b"], ""), "why": lk["note"]} for lk in links],
            "moved": [{"unit_id": m["unit_id"], "claim": claim.get(m["unit_id"], "")} for m in marks if m["kind"] == "moved"],
            "answers": [{"question": a["question"], "answer": a["answer"]} for a in asks],
            "archive": [{"unit_id": u["unit_id"], "claim": u["claim"]} for u in old_sunk[:20]]}
