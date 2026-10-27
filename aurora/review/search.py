"""Finding the papers of a field, after Academic Research Skills (ARS): scope first, criteria before searching,
two independent screenings with adjudication, coverage and skew checks, and the researcher's say at two points.

The protocol text comes from ARS itself (``aurora/third_party/ars``, CC BY-NC 4.0, unmodified): the sections
on scoping a question, on the search strategy and its documentation, and the screening decision rules are given
to the models as their instructions. This module runs the steps:

1. **Scope**: the direction decomposed, its question, what is in and out of scope, sub-questions, and the
   inclusion criteria and ordered exclusion codes, fixed before any search. The researcher confirms or amends.
2. **Discover**: the researcher's own corpus first (papers of the conversation and of the space's literature),
   then a strong model searching the web for each sub-question (surveys, curated lists, seminal and recent work),
   the landmarks it knows, and the scholarly indexes. Every paper is checked against the indexes; one that is
   not found is dropped.
3. **Screen**: two models of different families label each record from its title and abstract alone
   (include, unclear, exclude with the first failing code); disagreements go to the strong model. Unclear
   records are read in full text. Sub-questions with too few papers, and a skew of time or of preprints, get
   one more targeted search. The researcher sees the counts at each stage and the included papers, and can add
   or remove before the map goes on.
"""

from __future__ import annotations

import logging
import math
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any

from aurora.literature.library import library
from aurora.review import llm
from aurora.review.store import review_store

logger = logging.getLogger(__name__)

ARS = Path(__file__).resolve().parent.parent / "third_party" / "ars"
BATCH = 25
MIN_PER_SUB = 3
SKEW = 0.7


# ---------------------------------------------------------------------------
# The ARS protocol, as the models read it


def ars_section(file: str, start: str, stop: str | None = None) -> str:
    """The text of an ARS file from the heading ``start`` up to (not including) the heading ``stop``."""
    path = ARS / file
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8")
    i = text.find(start)
    if i < 0:
        return ""
    j = text.find(stop, i + len(start)) if stop else -1
    return text[i: j if j > 0 else len(text)].strip()


def scoping_protocol() -> str:
    return "\n\n".join(x for x in (
        ars_section("research_question_agent.md", "## Core Principles", "## FINER Framework"),
        ars_section("research_question_agent.md", "### Step 1: Topic Decomposition", "### Step 2: Question Generation"),
        ars_section("research_question_agent.md", "### Step 4: Scope Definition", "## Output Format"),
        ars_section("bibliography_agent.md", "### Step 1: Define Search Parameters", "### Step 2: Execute Search"),
        ars_section("bibliography_agent.md", "### Step 3: Apply Inclusion/Exclusion Criteria", "### Step 4: Source Screening"),
    ) if x)


def screening_protocol(full_text: bool = False) -> str:
    parts = [ars_section("decision_rules.md", "## 1. Labels and codes", "## 2. Title/abstract rules"),
             ars_section("decision_rules.md", "## 2. Title/abstract rules", "## 3. Conflicts")]
    if full_text:
        parts = [parts[0], ars_section("decision_rules.md", "## 5. Full-text rules")]
    return "\n\n".join(x for x in parts if x)


def adjudication_protocol() -> str:
    return ars_section("decision_rules.md", "## 3. Conflicts", "## 4. Edge cases")


# ---------------------------------------------------------------------------
# 1. Scope


def _scope_text(scope: dict[str, Any]) -> str:
    subs = "\n".join(f"- {s['id']}: {s['text']}" for s in scope.get("subquestions") or [])
    exc = "\n".join(f"- {e['code']}: {e['text']}" for e in (scope.get("criteria") or {}).get("exclude") or [])
    inc = "\n".join(f"- {x}" for x in (scope.get("criteria") or {}).get("include") or [])
    return (f"Question the map answers: {scope.get('question', '')}\n"
            f"In scope: {'; '.join(scope.get('in_scope') or [])}\nOut of scope: {'; '.join(scope.get('out_of_scope') or [])}\n"
            f"Timeframe: {scope.get('timeframe', '')}\nSub-questions:\n{subs}\nInclusion criteria:\n{inc}\n"
            f"Exclusion codes, in order:\n{exc}")


async def step_scope(b: Any, feedback: str = "") -> None:
    """The direction scoped, with criteria fixed before any search (``feedback``: the researcher's amendment)."""
    prev = b.content.get("scope")
    chat = _conversation(b)
    said = f"The conversation so far:\n{chat}" if chat else ""
    amend = f"The current scope:\n{_scope_text(prev)}\n\nThe researcher amends it: {feedback}" if prev and feedback else ""
    seed = b.content.get("settled") or {}
    settled = (f"""The researcher settled their own research question in a guided conversation; the map serves it. \
Build the scope from it and do not ask them again ("ask" is empty): the question the map answers is the field's side \
of theirs; their focus is in scope and what they left out is out of scope; the sub-questions are the parts of the \
field their question touches.
Their research question: {seed.get('question', '')}
Their focus: {'; '.join(seed.get('focus') or []) or '(not stated)'}
Left out by them: {'; '.join(seed.get('excluded') or []) or '(nothing)'}
Still open for them: {'; '.join(seed.get('to_confirm') or []) or '(nothing)'}""" if seed.get("question") else "")
    body = f"""You scope a research direction before its literature is searched, following this protocol (from \
Academic Research Skills). The direction is a field to map, not a single study: apply the topic decomposition, the \
scope definition, the sub-questions, and the search parameters and inclusion and exclusion criteria to it.

PROTOCOL
{scoping_protocol()}
END OF PROTOCOL

Research direction: {b.topic}
What the researcher said: {b.brief or '(nothing more)'}
{settled}
{said}
{amend}

Give:
- "topic": the direction in a few words; "question": the one question the map answers;
- "concepts": the key concepts; "in_scope" and "out_of_scope": lists, each item specific, out-of-scope items with a \
reason; "timeframe"; "assumptions";
- "subquestions": two to four, each a part of the field the map must cover (id s1, s2, ...);
- "criteria": "include", what a paper must be to belong (as a list), and "exclude", exclusion codes in the order a \
screener checks them, cheapest and most objective first (code E1, E2, ..., each one sentence). Building blocks \
used by the field but not about it (a general vision-language model, a generic retriever) are excluded unless the \
direction is about them;
- "keywords": search terms with synonyms; "document_types";
- "ask": at most two short questions for the researcher whose answers would change the scope (empty if none).

JSON: {{"topic": "...", "question": "...", "concepts": ["..."], "in_scope": ["..."], "out_of_scope": ["..."], \
"timeframe": "...", "assumptions": ["..."], "subquestions": [{{"id": "s1", "text": "..."}}], \
"criteria": {{"include": ["..."], "exclude": [{{"code": "E1", "text": "..."}}]}}, "keywords": ["..."], \
"document_types": "...", "ask": ["..."]}}"""
    out = await llm.ask(b.access, "scope", body)
    subs = [{"id": f"s{i + 1}", "text": str(x.get("text", ""))} for i, x in enumerate(out.get("subquestions") or [])
            if isinstance(x, dict) and x.get("text")][:4]
    crit = out.get("criteria") or {}
    exc = [{"code": f"E{i + 1}", "text": str(x.get("text", ""))} for i, x in enumerate(crit.get("exclude") or [])
           if isinstance(x, dict) and x.get("text")]
    b.content["scope"] = {
        "topic": str(out.get("topic") or b.topic), "question": str(out.get("question", "")),
        "concepts": [str(x) for x in out.get("concepts") or []], "in_scope": [str(x) for x in out.get("in_scope") or []],
        "out_of_scope": [str(x) for x in out.get("out_of_scope") or []], "timeframe": str(out.get("timeframe", "")),
        "assumptions": [str(x) for x in out.get("assumptions") or []], "subquestions": subs,
        "criteria": {"include": [str(x) for x in crit.get("include") or []], "exclude": exc},
        "keywords": [str(x) for x in out.get("keywords") or []][:16], "document_types": str(out.get("document_types", "")),
        "ask": [] if seed.get("question") else [str(x) for x in out.get("ask") or []][:2], "amended": [*((prev or {}).get("amended") or []), feedback] if feedback else [],
    }
    b.log("scope", f"{len(subs)} sub-questions, {len(exc)} exclusion codes" + (" (amended)" if feedback else ""))


def _conversation(b: Any, n: int = 12) -> str:
    """The last messages of the conversation the map was started from, for the scope."""
    m = review_store.map(b.map_id)
    if not m or not m.get("chat_id"):
        return ""
    from aurora.brainstorm.store import bs_store
    msgs = [x for x in bs_store.messages(m["chat_id"]) if x["kind"] == "text" and x["text"]][-n:]
    return "\n".join(f"{'Researcher' if x['who'] not in ('Skeptic', 'Methods', 'Librarian') else x['who']}: {x['text'][:600]}"
                     for x in msgs)


# ---------------------------------------------------------------------------
# 2. Discover


def _ref_of(x: dict[str, Any]) -> Any:
    from aurora.literature.check import Reference
    year = int(x["year"]) if str(x.get("year", "")).isdigit() else None
    return Reference(title=str(x.get("title", ""))[:400], authors=str(x.get("first_author") or x.get("author") or ""),
                     year=year, doi=str(x.get("doi") or ""), arxiv_id=str(x.get("arxiv_id") or ""))


async def _verify(b: Any, items: list[dict[str, Any]], source: str, pool: dict[str, dict[str, Any]], sub: str = "") -> int:
    """Check named papers against the indexes; those found join the pool. Returns how many were not found."""
    async def one(x: dict[str, Any]) -> Any:
        try:
            return await b.lit.check_reference(_ref_of(x))
        except Exception as exc:
            return exc

    results = await llm.gather_limited([one(x) for x in items if x.get("title") or x.get("doi") or x.get("arxiv_id")], limit=4)
    missing = 0
    for res in results:
        if isinstance(res, Exception) or not getattr(res, "verified", False) or res.paper is None:
            missing += 1
            continue
        e = library.upsert_paper(res.paper)
        _add(pool, e["paper_id"], source, sub)
    return missing


def _add(pool: dict[str, dict[str, Any]], pid: str, source: str, sub: str = "") -> None:
    row = pool.setdefault(pid, {"paper_id": pid, "sources": [], "subs": []})
    if source not in row["sources"]:
        row["sources"].append(source)
    if sub and sub not in row["subs"]:
        row["subs"].append(sub)


async def _web_round(b: Any, scope: dict[str, Any], aim: str, sub: str = "") -> list[dict[str, Any]]:
    """The strong model searches the web for papers with one aim; it names them, the indexes check them."""
    body = f"""Search the web for academic papers. Search broad first, then narrow; prefer primary sources: papers \
on arXiv, in proceedings and journals, curated paper lists and survey reference lists, over blogs and news.

The field: {scope.get('topic')}
{_scope_text(scope)}

Find: {aim}

List every paper you find that meets the inclusion criteria, with its exact title, year, first author, and arXiv \
ID or DOI when you saw one. Include surveys and benchmarks too, marked as such. Do not list a paper you did not see \
in the search results.

JSON: {{"papers": [{{"title": "...", "year": 2024, "first_author": "...", "arxiv_id": "", "doi": "", \
"kind": "method|survey|benchmark|system|other"}}]}}"""
    out = await llm.ask(b.access, "discover", body, web=8, max_tokens=12000, want="papers")
    papers = out.get("papers") if isinstance(out, dict) else out
    return [p for p in papers or [] if isinstance(p, dict)][:40]


async def discover(b: Any, scope: dict[str, Any], *, subs: list[dict[str, Any]] | None = None, aims: list[str] | None = None,
                   pool: dict[str, dict[str, Any]], prisma: dict[str, Any]) -> None:
    """One round of discovery for the given sub-questions (and extra aims); papers found go into the pool."""
    ident = prisma.setdefault("identified", {})
    jobs: list[tuple[str, str]] = [(f"papers on this sub-question: {s['text']}", s["id"]) for s in subs or []]
    jobs += [(a, "") for a in aims or []]
    rounds = await llm.gather_limited([_web_round(b, scope, aim, sub) for aim, sub in jobs], limit=3)
    for (aim, sub), found in zip(jobs, rounds):
        if isinstance(found, Exception):
            b.log("papers", f"web search failed for “{aim[:60]}”: {found}")
            continue
        missing = await _verify(b, found, "web", pool, sub)
        ident["web"] = ident.get("web", 0) + len(found)
        prisma["not_found"] = prisma.get("not_found", 0) + missing
        b.log("papers", f"web: {len(found)} named for “{aim[:70]}”, {len(found) - missing} found in the indexes")


async def step_papers(b: Any, seeds: list[str] | None = None) -> None:
    scope = b.content.get("scope") or {}
    pool: dict[str, dict[str, Any]] = {}
    prisma: dict[str, Any] = {"identified": {}, "not_found": 0}
    # the researcher's corpus first
    corpus = list(dict.fromkeys([*(seeds or []), *_conversation_papers(b), *_space_papers(b)]))
    for pid in corpus:
        _add(pool, pid, "your corpus")
    prisma["identified"]["your corpus"] = len(corpus)
    # the web, per sub-question, plus surveys and curated lists
    await discover(b, scope, subs=scope.get("subquestions") or [], pool=pool, prisma=prisma,
                   aims=["recent surveys of the field and curated lists of its papers (for example GitHub awesome lists), "
                         "and the papers they treat as central"])
    # the landmarks the model knows (checked like the rest)
    body = f"""{_scope_text(scope)}

List up to 20 landmark and representative papers of this field you are confident exist, from every period of its \
history, by exact title, first author and year. They are checked in the indexes; one that is not found is dropped.
JSON: {{"papers": [{{"title": "...", "first_author": "...", "year": 2020}}]}}"""
    try:
        out = await llm.ask(b.access, "landmarks", body, want="papers")
        named = [x for x in out.get("papers") or [] if isinstance(x, dict)][:20]
        missing = await _verify(b, named, "known", pool)
        prisma["identified"]["known to the model"] = len(named)
        prisma["not_found"] += missing
    except llm.ModelError as exc:
        b.log("papers", f"landmarks failed: {exc}")
    # the scholarly indexes, by keyword
    kw = scope.get("keywords") or [b.topic]
    n_idx = 0
    for q in [" ".join(kw[i:i + 3]) for i in range(0, min(len(kw), 9), 3)] or [b.topic]:
        try:
            hits = await b.lit.search(q, by="Field map", context=f"Map · {b.topic[:40]}", limit=8)
        except Exception as exc:
            b.log("papers", f"index search “{q}” failed: {exc}")
            continue
        n_idx += len(hits)
        for e in hits:
            _add(pool, e["paper_id"], "indexes")
    prisma["identified"]["scholarly indexes"] = n_idx
    prisma["after_dedupe"] = len(pool)
    b.content["pool"] = pool
    b.content["prisma"] = prisma
    if not pool:
        raise llm.ModelError("No paper was found; the web search or the indexes may be unreachable")
    b.log("papers", f"{sum(prisma['identified'].values())} records identified, {len(pool)} distinct papers after "
                    f"checking and removing duplicates ({prisma['not_found']} named but not found, dropped)")


def _conversation_papers(b: Any) -> list[str]:
    m = review_store.map(b.map_id)
    if not m or not m.get("chat_id"):
        return []
    from aurora.brainstorm.store import bs_store
    out = [c["paper_id"] for c in bs_store.cards(b.space) if c["paper_id"] and c["chat_id"] == m["chat_id"]]
    for msg in bs_store.messages(m["chat_id"]):
        for key in re.findall(r"\[([A-Za-z][\w-]*\d{2}[a-z]?)\]", msg["text"] or ""):
            if e := library.paper(key):
                out.append(e["paper_id"])
    for key in re.findall(r"\[([A-Za-z][\w-]*\d{2}[a-z]?)\]", b.brief or ""):
        if e := library.paper(key):
            out.append(e["paper_id"])
    return list(dict.fromkeys(out))


def _space_papers(b: Any) -> list[str]:
    owner = "" if b.campaign_id else b.owner
    return [i["paper_id"] for i in library.items(b.campaign_id or "", owner=owner)][:60]


# ---------------------------------------------------------------------------
# 3. Screen


def _record(pid: str) -> str:
    e = library.paper(pid)
    if e is None:
        return ""
    p = e["paper"]
    abstract = " ".join((p.abstract or "").split()[:220]) or "(no abstract)"
    return f"[{e['key']}] {p.title}. {p.venue or 'venue unknown'}, {p.year or 'n.d.'}.\n{abstract}"


def _labels(out: Any, keys: dict[str, str], codes: set[str]) -> dict[str, dict[str, Any]]:
    got: dict[str, dict[str, Any]] = {}
    for r in (out.get("records") if isinstance(out, dict) else out) or []:
        if not isinstance(r, dict) or (k := str(r.get("id", "")).strip("[] ")) not in keys:
            continue
        d = str(r.get("d", "")).lower()
        code = str(r.get("code", "")).upper()
        # a label and its code must agree, else the decision is discarded (as the rules require)
        if d == "include":
            code = "INC"
        elif d == "unclear":
            code = "UNC"
        elif d != "exclude" or code not in codes:
            continue
        got[keys[k]] = {"d": d, "code": code, "why": " ".join(str(r.get("why", "")).split()[:15]),
                        "subs": [s for s in r.get("subs") or [] if isinstance(s, str)],
                        "kind": str(r.get("kind", "method")).lower()}
    return got


async def _screen_batch(access: llm.Access, scope: dict[str, Any], pids: list[str], *, full_text: dict[str, str] | None = None) -> dict[str, dict[str, Any]]:
    keys = {library.paper(p)["key"]: p for p in pids if library.paper(p)}
    codes = {e["code"] for e in (scope.get("criteria") or {}).get("exclude") or []}
    records = "\n\n".join(f"{_record(p)}" + (f"\nFull text (excerpt):\n{full_text[p][:6000]}" if full_text and p in full_text else "")
                          for p in keys.values())
    body = f"""You screen records for a literature map, following these decision rules (from Academic Research Skills):

RULES
{screening_protocol(full_text=bool(full_text))}
END OF RULES

The protocol:
{_scope_text(scope)}

For each record give: "id" (its key), "d" (include, unclear or exclude), "code" (for exclude: the first failing \
exclusion code in the order above), "why" (at most 15 words, the deciding fact), "subs" (the sub-question ids it \
bears on), and "kind" (method, system, survey, benchmark, foundation for a building block the field uses, or other).

RECORDS
{records}

JSON: {{"records": [{{"id": "Key", "d": "include", "code": "", "why": "...", "subs": ["s1"], "kind": "method"}}]}}"""
    out = await llm.ask(access, "screen_fulltext" if full_text else "screen", body, temperature=0, max_tokens=12000, want="records")
    return _labels(out, keys, codes)


async def screen(b: Any, scope: dict[str, Any], pids: list[str], prisma: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Two independent screenings of title and abstract, conflicts adjudicated; returns {paper_id: final decision}."""
    a_model, b_model = (b.screeners + b.screeners)[:2] if b.screeners else (b.access, b.access)
    batches = [pids[i:i + BATCH] for i in range(0, len(pids), BATCH)]
    first = await llm.gather_limited([_screen_batch(m, scope, batch) for batch in batches for m in (a_model, b_model)], limit=6)
    ra: dict[str, dict[str, Any]] = {}
    rb: dict[str, dict[str, Any]] = {}
    for i, res in enumerate(first):
        if isinstance(res, Exception):
            b.log("screen", f"a screening batch failed: {res}")
            continue
        (ra if i % 2 == 0 else rb).update(res)
    final: dict[str, dict[str, Any]] = {}
    conflicts: list[str] = []
    order = [e["code"] for e in (scope.get("criteria") or {}).get("exclude") or []]
    for pid in pids:
        x, y = ra.get(pid), rb.get(pid)
        if x is None and y is None:
            final[pid] = {"d": "pending", "code": "", "why": "not screened (both screenings failed)", "subs": [], "kind": ""}
            continue
        if x is None or y is None:            # one screening failed: the other decides, and it is marked
            final[pid] = {**(x or y), "by": "one reviewer"}
            continue
        adv_x, adv_y = x["d"] != "exclude", y["d"] != "exclude"
        if adv_x and adv_y:
            final[pid] = {**(x if x["d"] == "include" else y), "subs": sorted(set(x["subs"]) | set(y["subs"])), "by": "both"}
        elif not adv_x and not adv_y:
            code = min((x["code"], y["code"]), key=lambda c: order.index(c) if c in order else 99)
            final[pid] = {**x, "code": code, "by": "both"}
        else:
            conflicts.append(pid)
            final[pid] = {"d": "conflict", "a": x, "b": y}
    if conflicts:
        adj = await _adjudicate(b, scope, conflicts, final)
        final.update(adj)
    prisma["screened"] = len(pids)
    prisma["conflicts"] = len(conflicts)
    agree = sum(1 for p in pids if p in ra and p in rb and (ra[p]["d"] != "exclude") == (rb[p]["d"] != "exclude"))
    both = sum(1 for p in pids if p in ra and p in rb)
    prisma["agreement"] = round(agree / both, 3) if both else None
    return final


async def _adjudicate(b: Any, scope: dict[str, Any], pids: list[str], final: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    keys = {library.paper(p)["key"]: p for p in pids if library.paper(p)}
    codes = {e["code"] for e in (scope.get("criteria") or {}).get("exclude") or []}
    records = "\n\n".join(f"{_record(p)}\nReviewer A: {final[p]['a']['d']}{(' ' + final[p]['a']['code']) if final[p]['a']['d'] == 'exclude' else ''}; "
                          f"reviewer B: {final[p]['b']['d']}{(' ' + final[p]['b']['code']) if final[p]['b']['d'] == 'exclude' else ''}"
                          for p in keys.values())
    body = f"""You adjudicate screening conflicts for a literature map, following these rules (from Academic Research \
Skills):

RULES
{adjudication_protocol()}

{screening_protocol()}
END OF RULES

The protocol:
{_scope_text(scope)}

The records the two reviewers disagree on, with their labels:
{records}

JSON: {{"records": [{{"id": "Key", "d": "include", "code": "", "why": "...", "subs": ["s1"], "kind": "method"}}]}}"""
    out = await llm.ask(b.access, "adjudicate", body, temperature=0, want="records")
    got = _labels(out, keys, codes)
    return {p: {**got[p], "by": "adjudicated"} if p in got else
            {**final[p]["a"], "by": "adjudication failed: reviewer A kept"} for p in pids}


async def full_text_pass(b: Any, scope: dict[str, Any], decisions: dict[str, dict[str, Any]], prisma: dict[str, Any]) -> None:
    """Records left unclear from their abstract are judged on their full text (as the rules' second pass does)."""
    from aurora.review.fieldmap import _paper_text
    unclear = [p for p, d in decisions.items() if d.get("d") == "unclear"][:30]
    if not unclear:
        return
    texts: dict[str, str] = {}
    for p in unclear:
        text, source = await _paper_text(b, p)
        if source == "pdf":
            texts[p] = text
    for i in range(0, len(unclear), 8):
        batch = unclear[i:i + 8]
        try:
            got = await _screen_batch(b.access, scope, batch, full_text=texts)
        except llm.ModelError as exc:
            b.log("screen", f"a full-text batch failed: {exc}")
            continue
        for p in batch:
            if p in got:
                decisions[p] = {**got[p], "by": "full text" if p in texts else "abstract (no open full text)"}
    prisma["full_text_assessed"] = len(unclear)
    prisma["full_text_excluded"] = sum(1 for p in unclear if decisions[p].get("d") == "exclude")


def skew(pids: list[str]) -> list[dict[str, Any]]:
    """Distributional skew advisories (any value with 70% or more of the known entries), as ARS describes them."""
    years = [library.paper(p)["paper"].year for p in pids if library.paper(p) and library.paper(p)["paper"].year]
    out = []
    if len(years) >= 5:
        latest = max(years)
        recent = sum(1 for y in years if y >= latest - 2)
        if recent / len(years) >= SKEW:
            out.append({"dimension": "time", "value": f"{latest - 2}–{latest}", "n": recent, "known": len(years)})
    venues = []
    for p in pids:
        e = library.paper(p)
        if e:
            v = (e["paper"].venue or "").lower()
            venues.append("preprint" if not v or "arxiv" in v or "biorxiv" in v else "published")
    if len(venues) >= 5 and (n := venues.count("preprint")) / len(venues) >= SKEW:
        out.append({"dimension": "venue", "value": "preprints", "n": n, "known": len(venues)})
    return out


def _rank(pid: str, d: dict[str, Any]) -> float:
    e = library.paper(pid)
    p = e["paper"] if e else None
    cites = (p.cited_by or 0) if p else 0
    age = max(1, (time.gmtime().tm_year - (p.year or time.gmtime().tm_year)) + 1) if p else 1
    agree = 2.0 if d.get("by") == "both" and d.get("d") == "include" else 1.0
    return agree + math.log1p(cites / age)


async def step_screen(b: Any) -> None:
    scope = b.content.get("scope") or {}
    pool: dict[str, dict[str, Any]] = b.content.get("pool") or {}
    prisma: dict[str, Any] = b.content.get("prisma") or {}
    decisions = await screen(b, scope, list(pool), prisma)
    await full_text_pass(b, scope, decisions, prisma)

    def included() -> list[str]:
        return [p for p, d in decisions.items() if d.get("d") in ("include", "unclear")]

    # coverage: sub-questions with too few papers, and a skew of time or of preprints, get one targeted round
    subs = scope.get("subquestions") or []
    per_sub = Counter(s for p in included() for s in decisions[p].get("subs") or [])
    thin = [s for s in subs if per_sub.get(s["id"], 0) < MIN_PER_SUB]
    advisories = skew([p for p in included() if decisions[p].get("kind") not in ("survey", "benchmark")])
    aims = []
    for a in advisories:
        if a["dimension"] == "time":
            aims.append(f"foundational and earlier work, published before {a['value'].split('–')[0]}")
        elif a["dimension"] == "venue":
            aims.append("peer-reviewed work in journals and conference proceedings")
    if thin or aims:
        before = set(pool)
        await discover(b, scope, subs=thin, aims=aims, pool=pool, prisma=prisma)
        new = [p for p in pool if p not in before]
        if new:
            more = await screen(b, scope, new, {})
            await full_text_pass(b, scope, more, {})
            decisions.update(more)
            prisma["screened"] = prisma.get("screened", 0) + len(new)
        b.log("screen", f"targeted round for {', '.join(s['id'] for s in thin) or 'skew'}: {len(new)} new papers")
    prisma["after_dedupe"] = len(pool)
    inc = included()
    prisma["excluded_by_code"] = dict(Counter(d["code"] for d in decisions.values() if d.get("d") == "exclude"))
    prisma["included"] = len(inc)
    b.content["prisma"] = prisma
    b.content["screening"] = {p: {k: v for k, v in d.items() if k in ("d", "code", "why", "subs", "kind", "by")}
                              for p, d in decisions.items()}
    b.content["coverage"] = {"per_sub": {s["id"]: sum(1 for p in inc if s["id"] in (decisions[p].get("subs") or [])) for s in subs},
                             "skew": skew([p for p in inc if decisions[p].get("kind") not in ("survey", "benchmark")])}
    choose(b)
    b.log("screen", f"{prisma['screened']} screened (agreement {prisma.get('agreement')}), {prisma.get('conflicts', 0)} "
                    f"adjudicated, {len(inc)} included")


def choose(b: Any) -> None:
    """The map's papers from the included ones: core methods (some held out), surveys, benchmarks, building blocks."""
    decisions = b.content.get("screening") or {}
    removed = set(b.content.get("removed") or [])
    added = [p for p in b.content.get("added") or [] if p not in removed]
    inc = [p for p, d in decisions.items() if d.get("d") in ("include", "unclear") and p not in removed]
    kind = {p: decisions[p].get("kind", "method") for p in inc}
    methods = sorted([p for p in inc if kind[p] in ("method", "system", "other", "")], key=lambda p: -_rank(p, decisions[p]))
    # balance the sub-questions: the best of each first, then the rest by rank
    subs = [s["id"] for s in (b.content.get("scope") or {}).get("subquestions") or []]
    picked: list[str] = list(added)
    for s in subs:
        for p in [x for x in methods if s in (decisions[x].get("subs") or [])][:3]:
            if p not in picked:
                picked.append(p)
    for p in methods:
        if len(picked) >= b.size:
            break
        if p not in picked:
            picked.append(p)
    picked = picked[: max(b.size, len(added))]
    from aurora.review.fieldmap import HELD_OUT
    def year(p: str) -> int:
        e = library.paper(p)
        return (e["paper"].year or 0) if e else 0

    by_year = sorted([p for p in picked if p not in added], key=lambda p: -year(p))
    n_held = min(HELD_OUT, max(2, round(len(picked) * 0.15))) if len(picked) >= 6 else 0
    rest = by_year[2:]
    held = (by_year[:min(2, n_held)] + rest[1::max(2, len(rest) // max(1, n_held - 2))][: max(0, n_held - 2)]) if n_held else []
    for r in review_store.papers(b.map_id):                 # a re-chosen set replaces the last one
        review_store._x("DELETE FROM rv_papers WHERE map_id=? AND paper_id=?", (b.map_id, r["paper_id"]))
    for p in picked:
        review_store.add_paper(b.map_id, p, "held_out" if p in held else "core")
    for role, kinds, cap in (("survey", ("survey",), 3), ("benchmark", ("benchmark",), 4), ("foundation", ("foundation",), 4)):
        for p in sorted([x for x in inc if kind[x] in kinds], key=lambda x: -_rank(x, decisions[x]))[:cap]:
            review_store.add_paper(b.map_id, p, role)
    b.content["chosen_papers"] = picked


async def amend_corpus(b: Any, text: str) -> dict[str, Any]:
    """The researcher's word on the papers ("add X", "drop Y", "fewer surveys"): applied, and said what changed."""
    rows = {r["paper_id"]: r["role"] for r in review_store.papers(b.map_id)}
    lines = "\n".join(f"[{library.paper(p)['key']}] {library.paper(p)['paper'].title} ({role})" for p, role in rows.items() if library.paper(p))
    out = await llm.ask(b.access, "corpus_edit", f"""The papers chosen for a map of "{b.topic}":
{lines}

The researcher says: "{text}"

Which papers to remove (their keys), and which to add (exact titles, first author, year, as the researcher means \
them; you may name well-known papers they refer to by a short name)?
JSON: {{"remove": ["Key"], "add": [{{"title": "...", "first_author": "...", "year": 2024}}], "note": "what you did, in a sentence"}}""",
                          temperature=0)
    removed, added_ids = [], []
    for k in out.get("remove") or []:
        e = library.paper(str(k).strip("[] "))
        if e and e["paper_id"] in rows:
            removed.append(e["paper_id"])
    pool: dict[str, dict[str, Any]] = {}
    missing = await _verify(b, [x for x in out.get("add") or [] if isinstance(x, dict)], "you", pool)
    added_ids = list(pool)
    b.content["removed"] = sorted(set(b.content.get("removed") or []) | set(removed))
    b.content["added"] = [p for p in dict.fromkeys([*(b.content.get("added") or []), *added_ids])]
    screening = b.content.setdefault("screening", {})
    for p in added_ids:
        screening[p] = {"d": "include", "code": "INC", "why": "added by the researcher", "subs": [], "kind": "method", "by": "you"}
    for p in removed:
        if p in screening:
            screening[p] = {**screening[p], "d": "exclude", "code": "YOU", "why": "removed by the researcher", "by": "you"}
    choose(b)
    return {"removed": removed, "added": added_ids, "not_found": missing, "note": str(out.get("note", ""))}
