"""Zoom in: a deep read of one paper, as twelve questions, each answered in its own call from the PDF.

The order matters: the problem and what came before (1, 2), then the authors' likely path to the idea
(3), only then the idea, the method, its mathematics and experiments (4 to 8), and last what would break
it and where to go next (9 to 12).

Question 3 is answered from what the authors could have known: the introduction with the paragraphs that
state the paper's own contributions removed, the related work, and abstracts of earlier papers (from the
map or the project's literature). It never sees the method or the experiments. A separate call then checks
each step of the reconstruction against what the authors themselves wrote, so the reader can tell the
authors' stated motivation from the reconstruction's own guesses. The reader writes their own guess first.

Every paragraph says what kind of claim it is; quotes are kept only when the paper contains them, with
their page, so the reader can show the passage.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from aurora.literature.annotations import find_page, fold_text
from aurora.literature.library import library
from aurora.review import llm
from aurora.review.llm import PARAGRAPHS, Access, paragraphs
from aurora.review.store import review_store

logger = logging.getLogger(__name__)

PAPER_CHARS = 60_000
QUESTIONS: list[dict[str, Any]] = [
    {"n": 1, "title": "What problem does the paper pose, why does it matter, and what is solving it worth?",
     "ask": "What research problem does the paper pose and solve? Add the background a newcomer needs. Why is the problem "
            "important, and what value would solving it bring?"},
    {"n": 2, "title": "Was it solved before? Why was earlier work not enough?",
     "ask": "Had the problem been solved before? What did earlier work do, and exactly where and why did it fall short?"},
    {"n": 3, "title": "Before the method: how might the authors have got to the idea?", "key": True},
    {"n": 4, "title": "The intuition, in one breath",
     "ask": "What is the intuition behind the method? Give the essence of the core idea, clearly and briefly, so it "
            "sticks. One or two paragraphs."},
    {"n": 5, "title": "The method, on a real example: input, processing, output",
     "ask": "What exactly is the method? Explain it on one concrete, realistic example, following it from input through "
            "each processing step to output."},
    {"n": 6, "title": "The core mathematics, for someone who does not like mathematics",
     "ask": "What is the core mathematical derivation? Explain it from the ground up for a reader weak in mathematics: "
            "the theory it rests on and the intuition of each step. If the paper has no real derivation, say so in one "
            "paragraph and explain only the one formula or score that matters."},
    {"n": 7, "title": "How the experiments test the claims",
     "ask": "How do the experiments test the method and the claims? For each: the question asked, the experiment designed "
            "to answer it, and the answer. The core logic, not the numbers, though a decisive number may be named."},
    {"n": 8, "title": "Take-aways",
     "ask": "What are the take-aways a researcher should keep from this paper, beyond its method (which may be outdated "
            "in a few months)? Three to five."},
    {"n": 9, "title": "The weakest assumption", "key": True, "agent": "Skeptic",
     "ask": "What is the most fragile assumption of the paper, the one most likely to break first in practice? Say why "
            "it is fragile, and what happens to the results if it breaks."},
    {"n": 10, "title": "One week: the smallest reproduction of a core claim", "agent": "Methods",
     "ask": "With one week, what minimal experiment would test one core claim of the paper? Say which claim, the setup, "
            "what result would confirm or refute it, and what it costs."},
    {"n": 11, "title": "If I disagreed: the counterexample", "key": True, "agent": "Skeptic",
     "ask": "If you disagreed with this paper, which counterexamples would you design to test its claims? Concrete cases, "
            "inputs or settings in which the method or the claims should fail, and why."},
    {"n": 12, "title": "A follow-up idea, new and from a real limitation", "agent": "Librarian",
     "ask": "Propose one follow-up idea that is new and not incremental: start from a limitation of the method or an "
            "unmet need (use the weakest assumption and the counterexamples), and say what new value it would bring."},
]
TITLES = {q["n"]: q["title"] for q in QUESTIONS}


# ---------------------------------------------------------------------------
# The paper's text, and the part of it the authors' path may use


_HEAD = re.compile(r"^\s*(?:(\d{1,2})|([IVX]{1,4}))\.?\s+([A-Z][A-Za-z][A-Za-z ,:&/-]{1,70})\s*$", re.M)


def _normal(text: str) -> str:
    """Headings as PDFs extract them, made plain: small capitals split ("1 I NTRODUCTION"), a number on its own line."""
    text = re.sub(r"(?m)^\s*(\d{1,2}|[IVX]{1,4})\.?\s*\n(?=[A-Z])", r"\1 ", text)
    return re.sub(r"\b([A-Z]) (?=[A-Z]{2,})", r"\1", text)


def sections(text: str) -> dict[str, str]:
    """The introduction and the related work of a paper's text (by its numbered headings), when they can be found."""
    text = _normal(text)
    heads = [(m.start(), m.end(), m.group(3).strip().lower()) for m in _HEAD.finditer(text)]
    out: dict[str, str] = {}
    for i, (_start, end, title) in enumerate(heads):
        stop = heads[i + 1][0] if i + 1 < len(heads) else len(text)
        if "introduction" in title and "intro" not in out:
            out["intro"] = text[end:stop]
        elif re.search(r"related|background|prior work|preliminar", title) and "related" not in out:
            out["related"] = text[end:stop]
    if "intro" not in out:          # no headings found: the opening of the paper stands in for it
        out["intro"] = text[: min(len(text) // 6, 8000)]
    return out


_OWN = re.compile(r"\b(we (propose|present|introduce|develop|show|demonstrate|achieve|outperform)|our (method|approach|"
                  r"model|framework|system|contributions?|results?)|in this (paper|work)|this (paper|work) (proposes|presents|"
                  r"introduces))\b", re.I)


def without_contributions(intro: str, drop: set[int] | None = None) -> str:
    """The introduction without what the paper itself contributes: the paragraphs a model marked, and any sentence
    that announces the paper's own method or results."""
    paras = [p for p in re.split(r"\n\s*\n|(?<=\.)\n(?=[A-Z])", intro) if p.strip()]
    kept = []
    for i, p in enumerate(paras):
        if drop and i in drop:
            continue
        sentences = re.split(r"(?<=[.!?])\s+", " ".join(p.split()))
        kept.append(" ".join(s for s in sentences if not _OWN.search(s)))
    return "\n\n".join(k for k in kept if k.strip())


def _earlier_papers(paper: Any, paper_id: str, space: str, map_id: str, limit: int = 12) -> list[dict[str, Any]]:
    """Papers published before this one, from its map or its space's literature, closest in wording first."""
    pool: dict[str, dict[str, Any]] = {}
    if map_id:
        for r in review_store.papers(map_id):
            if e := library.paper(r["paper_id"]):
                pool[e["paper_id"]] = e
    campaign = "" if space.startswith("user:") else space
    owner = space.split(":", 1)[1] if space.startswith("user:") else ""
    for i in library.items(campaign, owner=owner):
        if e := library.paper(i["paper_id"]):
            pool.setdefault(e["paper_id"], e)
    words = set(fold_text(w) for w in (paper.title + " " + paper.abstract).split() if len(w) > 4)
    cands = [e for pid, e in pool.items() if pid != paper_id and e["paper"].year and paper.year
             and e["paper"].year < paper.year and e["paper"].abstract]
    cands.sort(key=lambda e: -len(words & set(fold_text(w) for w in (e["paper"].title + " " + e["paper"].abstract).split())))
    return cands[:limit]


# ---------------------------------------------------------------------------
# Answering


def _verify_quotes(items: list[dict[str, Any]], pages: list[str]) -> list[dict[str, Any]]:
    """Each paragraph's quote kept only if the paper has it, with its page."""
    out = []
    for p in items:
        q = " ".join(str(p.get("quote", "")).split())
        page = find_page(pages, q) if q else -1
        out.append({**p, "quote": q if page >= 0 else "", "page": page})
    return out


def _paras(raw: Any, pages: list[str]) -> list[dict[str, Any]]:
    base = paragraphs(raw or [])
    quotes = {str(p.get("text", "")): p.get("quote", "") for p in (raw or []) if isinstance(p, dict)}
    return _verify_quotes([{**p, "quote": quotes.get(p["text"], "")} for p in base], pages)


QPARAS = '[{"text": "...", "label": "paper|lit|inf|spec", "quote": "a few words copied from the paper, or empty"}]'


def _context(entry: dict[str, Any], text: str, map_note: str) -> str:
    p = entry["paper"]
    return (f"The paper [{entry['key']}]: {p.title} ({p.year or 'n.d.'}, {p.venue or 'venue unknown'}). "
            f"Authors: {', '.join(p.authors[:8])}{' et al.' if len(p.authors) > 8 else ''}.\n"
            + (f"\nWhere it sits in the map of its field:\n{map_note}\n" if map_note else "")
            + f"\nIts text:\n{text[:PAPER_CHARS]}")


async def answer(access: Access, q: dict[str, Any], entry: dict[str, Any], text: str, pages: list[str],
                 map_note: str = "", earlier: dict[int, dict[str, Any]] | None = None) -> dict[str, Any]:
    n = q["n"]
    persona = f"You answer as the {q['agent']} of Pippin's brainstorm.\n" if q.get("agent") else ""
    prior = ""
    if n == 12 and earlier:
        prior = "\n".join(f"Q{k} {TITLES[k]}:\n" + "\n".join(p["text"] for p in earlier.get(k, {}).get("paragraphs") or [])
                          for k in (9, 11) if earlier.get(k))
    extra, schema = "", f'{{"paragraphs": {QPARAS}}}'
    if n == 5:
        extra = "Also give the running example in one sentence, and the steps, each with what happens to the example."
        schema = f'{{"example": "...", "steps": [{{"what": "...", "example": "..."}}], "paragraphs": {QPARAS}}}'
    elif n == 6:
        schema = f'{{"none": false, "paragraphs": {QPARAS}}}'
    elif n == 7:
        schema = f'{{"rows": [{{"question": "...", "experiment": "...", "answer": "..."}}], "paragraphs": {QPARAS}}}'
    elif n == 10:
        schema = f'{{"claim": "...", "plan": [{{"day": "...", "do": "..."}}], "paragraphs": {QPARAS}}}'
    elif n == 12:
        schema = f'{{"idea": "the idea in one sentence", "from": "the limitation or need it starts from", "search": "a search query of three to six words for the closest existing work", "paragraphs": {QPARAS}}}'
    body = (f"{persona}{_context(entry, text, map_note)}\n\n"
            + (f"What the reading found so far:\n{prior}\n\n" if prior else "")
            + f"Question {n}: {q['ask']} {extra}\n\nWhere a paragraph rests on the paper, give in \"quote\" a few words copied "
              f"exactly from the text above.\n\nJSON: {schema}")
    out = await llm.ask(access, f"deep_read_q{n}", body, max_tokens=8000, want=None)
    if isinstance(out, list):           # a bare list: the rows of question 7, else the paragraphs
        out = {"rows": out} if n == 7 and out and isinstance(out[0], dict) and "question" in out[0] else {"paragraphs": out}
    res: dict[str, Any] = {"state": "done", "paragraphs": _paras(out.get("paragraphs"), pages)}
    if n == 5:
        res.update(example=str(out.get("example", "")),
                   steps=[s for s in out.get("steps") or [] if isinstance(s, dict) and s.get("what")][:8])
    elif n == 6:
        res["none"] = bool(out.get("none"))
    elif n == 7:
        res["rows"] = [r for r in out.get("rows") or [] if isinstance(r, dict) and r.get("question")][:8]
    elif n == 10:
        res.update(claim=str(out.get("claim", "")), plan=[s for s in out.get("plan") or [] if isinstance(s, dict)][:7])
    elif n == 12:
        res.update(idea=str(out.get("idea", "")), source=str(out.get("from", "")), search=str(out.get("search", "")))
    return res


async def authors_path(access: Access, entry: dict[str, Any], text: str, pages: list[str]) -> dict[str, Any]:
    """Question 3: the reconstruction from what came before, then a check against what the authors say."""
    p = entry["paper"]
    secs = sections(text)
    intro = secs.get("intro", "")
    paras = [x for x in re.split(r"\n\s*\n|(?<=\.)\n(?=[A-Z])", intro) if x.strip()]
    numbered = "\n\n".join(f"({i}) {' '.join(x.split())}" for i, x in enumerate(paras))
    drop: set[int] = set()
    if paras:
        got = await llm.ask(access, "contributions", f"""The introduction of a paper, by paragraph:
{numbered[:20000]}

Which paragraphs state the paper's own contributions, method, results or organisation (as opposed to the problem, \
the background and earlier work)?
JSON: {{"drop": [0]}}""", temperature=0, want="drop")
        drop = {int(i) for i in got.get("drop") or [] if str(i).isdigit()}
    before = without_contributions(intro, drop)
    related = " ".join(secs.get("related", "").split())[:12000]
    earlier = _earlier_papers(p, entry["paper_id"], entry.get("space", ""), entry.get("map_id", ""))
    abstracts = "\n".join(f"[{e['key']}] {e['paper'].title} ({e['paper'].year}): {' '.join(e['paper'].abstract.split()[:80])}"
                          for e in earlier)
    body = f"""You reconstruct how the authors of a paper may have come to its idea, from what they could know before it. \
You are NOT given the paper's method, results or contributions; do not guess them from hindsight. Use only the \
background, the failure modes of earlier work, empirical observations and related work below.

The paper's problem, as its introduction sets it out (its own contributions removed):
{before[:12000] or '(not available)'}

Its related work:
{related or '(not available)'}

Earlier papers in the field ({p.year} and before):
{abstracts or '(none known)'}

Reconstruct the path in three to five "moves": what the authors likely saw (a failure mode of earlier work, a new \
tool or result that had just appeared, a known structure that lacked one property, an observation), and the last \
move, the step that combines them into an idea. For each move: the text, its "kind" (failure mode, new tool, known \
structure, observation, combination) and a label (they are mostly "inf"; "lit" when the move cites an established \
result). Then "summary": one paragraph on why someone with this knowledge could have arrived at the idea, and what \
intuition or inspiration made the difference.

JSON: {{"moves": [{{"text": "...", "kind": "...", "label": "inf|lit"}}], "summary": {PARAGRAPHS}}}"""
    out = await llm.ask(access, "deep_read_q3", body, temperature=0.4, want="moves")
    moves = [{"text": " ".join(str(m.get("text", "")).split()), "kind": str(m.get("kind", "")),
              "label": m.get("label") if m.get("label") in ("inf", "lit", "spec") else "inf"}
             for m in out.get("moves") or [] if isinstance(m, dict) and m.get("text")][:6]
    # what the authors themselves say about where it came from
    lines = "\n".join(f"({i}) {m['text']}" for i, m in enumerate(moves))
    check = await llm.ask(access, "deep_read_q3_check", f"""A reconstruction of how the authors of [{entry['key']}] came to \
their idea, in moves:
{lines}

The paper's full introduction:
{' '.join(intro.split())[:15000]}

For each move, does the introduction state it (the authors' own motivation)? Answer "stated", "consistent" (not \
stated, but fits what they write) or "unsupported", with a few words copied from the introduction when stated. \
Then "authors_say": one or two paragraphs on what the authors themselves give as their motivation (label "paper").

JSON: {{"moves": [{{"i": 0, "support": "stated|consistent|unsupported", "quote": "..."}}], "authors_say": {QPARAS}}}""", temperature=0,
                            want="moves")
    for c in check.get("moves") or []:
        if isinstance(c, dict) and str(c.get("i", "")).isdigit() and int(c["i"]) < len(moves):
            m = moves[int(c["i"])]
            support = str(c.get("support", "")).lower()
            m["support"] = support if support in ("stated", "consistent", "unsupported") else "unsupported"
            quote = " ".join(str(c.get("quote", "")).split())
            page = find_page(pages, quote) if quote else -1
            m["quote"], m["page"] = (quote, page) if page >= 0 else ("", -1)
    return {"state": "done", "moves": moves, "summary": paragraphs(out.get("summary") or []),
            "authors_say": _paras(check.get("authors_say"), pages),
            "context": {"intro_paragraphs_dropped": len(drop), "had_related": bool(related),
                        "earlier": [e["key"] for e in earlier]}}


async def closest_to_idea(access: Access, lit: Any, res: dict[str, Any]) -> None:
    if not res.get("idea") or lit is None:
        return
    hits: list[dict[str, Any]] = []
    # the query as given, then shorter: indexes find little for long queries
    queries = [res.get("search") or "", " ".join((res.get("search") or res["idea"]).split()[:6]), " ".join(res["idea"].split()[:8])]
    for query in dict.fromkeys(q for q in queries if q.strip()):
        try:
            hits = await lit.search(query, by="Deep read", context="Closest to a follow-up idea", limit=6)
        except Exception as exc:
            res["novelty_note"] = f"The search failed: {exc}"
            return
        if hits:
            break
    if not hits:
        res["novelty"], res["novelty_note"] = "unknown", "The search found nothing; that is not proof nobody did it."
        return
    lines = "\n".join(f"[{e['key']}] {e['paper'].title} ({e['paper'].year}): {' '.join(e['paper'].abstract.split()[:70])}"
                      for e in hits)
    out = await llm.ask(access, "closest", f"""A follow-up research idea: {res['idea']}
Papers a search found:
{lines}

Which are closest, and how does the idea differ from each? Is it new ("yes"), partly done ("partly") or done ("no")?
JSON: {{"closest": [{{"key": "...", "difference": "..."}}], "novel": "yes|partly|no", "note": "..."}}""", temperature=0, want="closest")
    keys = {e["key"]: e["paper_id"] for e in hits}
    res["closest"] = [{**c, "key": k, "paper_id": keys[k]} for c in out.get("closest") or []
                      if isinstance(c, dict) and (k := str(c.get("key", "")).strip("[] ")) in keys][:4]
    novel = str(out.get("novel", "")).lower()
    res["novelty"] = novel if novel in ("yes", "partly", "no") else "unknown"
    res["novelty_note"] = str(out.get("note", ""))


def map_note(map_id: str, paper_id: str) -> str:
    """Where the paper sits in a map, and the open problems the map found (for questions 9 to 12)."""
    if not map_id:
        return ""
    m = review_store.map(map_id)
    if not m:
        return ""
    from aurora.review.fieldmap import chosen
    c = m["content"]
    fw = chosen(c)
    lines = [f"Direction: {m['topic']}"]
    if fw:
        p = {**fw["placements"], **fw["held"], **fw.get("joined", {})}.get(paper_id)
        if p:
            lines.append("Its place: " + "; ".join(
                f"{d['name']} = {next((v['label'] for v in d['values'] if v['id'] == p['values'].get(d['id'])), '?')}" for d in fw["dims"]))
    for pr in (c.get("problems") or [])[:4]:
        lines.append(f"Open problem: {pr['title']}")
    return "\n".join(lines)


async def read_all(access: Access, read_id: str, entry: dict[str, Any], text: str, pages: list[str], *,
                   only: list[int] | None = None, lit: Any = None) -> None:
    """Answer the questions (all, or ``only`` these), each in its own call; 12 waits for 9 and 11."""
    r = review_store.read_by_id(read_id)
    if r is None:
        return
    note = map_note(r["map_id"], entry["paper_id"])
    entry = {**entry, "space": r["space"], "map_id": r["map_id"]}
    todo = only or [q["n"] for q in QUESTIONS]
    for n in todo:
        review_store.set_answer(read_id, n, {"state": "pending"})

    async def one(n: int) -> None:
        try:
            if n == 3:
                res = await authors_path(access, entry, text, pages)
            else:
                done = (review_store.read_by_id(read_id) or {}).get("answers", {})
                earlier = {int(k): v for k, v in done.items() if v.get("state") == "done"}
                res = await answer(access, QUESTIONS[n - 1], entry, text, pages, note, earlier)
                if n == 12:
                    await closest_to_idea(access, lit, res)
        except Exception as exc:
            logger.info("deep read %s question %s failed", read_id, n, exc_info=True)
            res = {"state": "failed", "error": str(exc)[:300]}
        review_store.set_answer(read_id, n, res)

    first = [n for n in todo if n != 12]
    await llm.gather_limited([one(n) for n in first], limit=5)
    if 12 in todo:
        await one(12)
