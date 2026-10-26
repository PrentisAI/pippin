"""Zoom out: a map of a research direction, built as a chain where each step uses only what came before.

0. **Need**: why the thing studied is needed at all: what fails without it, in concrete tasks. Its
   product, the *capabilities* (what must be achieved), anchors everything after it.
1. **Definition**: how the communities use the term, where they disagree, and a working definition
   with explicit boundary decisions, split into components.
2. **Design space**: several models each propose dimensions, each dimension one component of the
   definition. Each framework is scored on the same papers: how many fit, how many held-out papers
   fit, how independent the dimensions are (computed), and how well they explain differences in
   success and cost (judged).
3. **Placement**: every paper placed with words from its card as evidence; held-out papers are placed
   separately; what does not fit becomes a proposal to revise the framework.
4. **Progress**: for each capability, how well it is solved, with evidence: best results, the
   conditions they need, limitations that recur across papers, conflicting results, cost, and where
   evaluation and need part.
5. **Trends**: the timeline with what drove each turn, and how papers move through the design space.
6. **Assumptions** the field shares without saying so.
7. **Problems** that are attempted but not well solved: chosen by importance and the distance to
   solved, diagnosed (why not solved), why now, approaches (sparse regions of the design space,
   adjacent fields, a dropped assumption), a first experiment, and the closest work, found by search.
8. **Read next**: the papers the judgements rest on most, with the deep-read questions to focus on.

Two loops run back up the chain: a paper that does not fit proposes a change to the dimensions (or the
definition); a person who challenges the definition gets its consequences first, then a rebuilt map.
Every change is a new version, with its reason.
"""

from __future__ import annotations

import logging
import math
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from aurora.literature.annotations import fold_text
from aurora.literature.library import library
from aurora.review import llm
from aurora.review.llm import PARAGRAPHS, Access, paragraphs
from aurora.review.store import review_store

logger = logging.getLogger(__name__)

STEPS = ("need", "papers", "cards", "definition", "frameworks", "progress", "trends", "assumptions", "problems",
         "read_next")
STEP_NAMES = {
    "need": "Why it is needed", "papers": "Gathering papers", "cards": "Reading the papers", "definition": "Definitions",
    "frameworks": "Design space", "progress": "How well it is solved", "trends": "Trends", "assumptions": "Shared assumptions",
    "problems": "Unsolved problems", "read_next": "What to read next",
}
CAUSES = ("signal", "data", "representation", "evaluation", "compute", "assumption", "definition")
STATUSES = ("solved", "partly", "early", "open")
CORE_SIZE = 30
HELD_OUT = 4
CARD_TEXT = 14_000


@dataclass
class Build:
    """What a step works with."""

    map_id: str
    space: str
    campaign_id: str
    owner: str
    topic: str
    brief: str
    access: Access
    seat_models: list[str] = field(default_factory=list)
    lit: Any = None
    content: dict[str, Any] = field(default_factory=dict)
    size: int = CORE_SIZE
    # told after each step, after a failure, and when a version is kept: on_step(step | "failed" | "version")
    on_step: Any = None
    # the space's ideas ({card_id, text}), placed on the map when it is built or rebuilt
    ideas: list[dict[str, Any]] = field(default_factory=list)

    def tell(self, what: str) -> None:
        if self.on_step is not None:
            try:
                self.on_step(what)
            except Exception:
                logger.info("telling the conversation about %s failed", what, exc_info=True)

    def log(self, step: str, text: str) -> None:
        review_store.log(self.map_id, step, text)

    def save(self) -> None:
        review_store.put_content(self.map_id, self.content)


# ---------------------------------------------------------------------------
# Papers as the steps see them


def _entry(pid: str) -> dict[str, Any] | None:
    return library.paper(pid)


def key_of(pid: str) -> str:
    e = _entry(pid)
    return e["key"] if e else pid


def _papers(map_id: str, roles: tuple[str, ...] = ("core", "joined")) -> list[dict[str, Any]]:
    out = []
    for r in review_store.papers(map_id):
        if r["role"] in roles and (e := _entry(r["paper_id"])):
            out.append({**r, "key": e["key"], "paper": e["paper"]})
    return out


def _by_key(map_id: str) -> dict[str, str]:
    return {key_of(r["paper_id"]): r["paper_id"] for r in review_store.papers(map_id)}


def _brief_line(p: dict[str, Any], abstract_words: int = 0) -> str:
    paper = p["paper"]
    line = f"[{p['key']}] {paper.title} ({paper.year or 'n.d.'}{', ' + paper.venue if paper.venue else ''})"
    if abstract_words and paper.abstract:
        words = paper.abstract.split()
        line += "\n  " + " ".join(words[:abstract_words]) + ("…" if len(words) > abstract_words else "")
    return line


def _card_line(p: dict[str, Any]) -> str:
    c = p["card"] or {}
    parts = [f"[{p['key']}] {p['paper'].title} ({p['paper'].year or 'n.d.'})"]
    if c.get("sub_problem"):
        parts.append(f"  problem: {c['sub_problem']}")
    if c.get("choice"):
        parts.append(f"  core choice: {c['choice']}")
    return "\n".join(parts)


def _capabilities(content: dict[str, Any]) -> str:
    caps = (content.get("need") or {}).get("capabilities") or []
    return "\n".join(f"- {c['id']}: {c['name']}. {c.get('info', '')} ({c.get('timescale', '')})" for c in caps)


def _verified(quote: str, text: str) -> bool:
    q = fold_text(quote)
    return len(q) >= 12 and q in fold_text(text)


# ---------------------------------------------------------------------------
# 0. Why it is needed


async def step_need(b: Build) -> None:
    body = f"""Research direction: {b.topic}
What the researcher said about it: {b.brief or '(nothing more)'}

Before any paper: why is this needed at all? Reason from tasks, not from papers.
- "term": the concept the direction studies, in one or two words (e.g. "memory").
- "why": two or three paragraphs: what goes wrong without it, and what makes it necessary.
- "examples": three to five concrete tasks, each with the information it needs that is not available when it is needed.
- "formal": one paragraph stating the problem in its standard formal frame, if there is one (name it and its source).
- "capabilities": four to eight things a solution must be able to do, derived from the tasks. Each: an id (c1, c2, ...), \
a short name, what information or ability it needs, its timescale, and an example. These are the yardstick for \
everything later, so make them distinct and checkable.

JSON: {{"term": "...", "why": {PARAGRAPHS}, "examples": [{{"task": "...", "needs": "..."}}], "formal": {PARAGRAPHS}, \
"capabilities": [{{"id": "c1", "name": "...", "info": "...", "timescale": "...", "example": "..."}}]}}"""
    out = await llm.ask(b.access, "need", body)
    caps = []
    for i, c in enumerate(out.get("capabilities") or []):
        if isinstance(c, dict) and c.get("name"):
            caps.append({"id": f"c{i + 1}", "name": str(c["name"]), "info": str(c.get("info", "")),
                         "timescale": str(c.get("timescale", "")), "example": str(c.get("example", ""))})
    b.content["need"] = {"term": str(out.get("term") or b.topic), "why": paragraphs(out.get("why") or []),
                         "examples": [e for e in out.get("examples") or [] if isinstance(e, dict)],
                         "formal": paragraphs(out.get("formal") or []), "capabilities": caps}
    b.log("need", f"{len(caps)} capabilities to measure the field by")


# ---------------------------------------------------------------------------
# Gathering and reading the papers


async def step_papers(b: Build, seeds: list[str] | None = None) -> None:
    body = f"""Research direction: {b.topic}
{('Scope and interest: ' + b.brief) if b.brief else ''}
Capabilities a solution needs:
{_capabilities(b.content)}

1. Write 8 to 12 search queries (a few specific words each) that together find: the foundational papers, the papers \
that changed how people approach the problem, recent surveys, high-impact work of the last two years, approaches \
that differ from the mainstream, and the closest work in adjacent fields.
2. List up to 12 landmark papers of this direction you are confident exist, from every period of its history, by \
exact title, first author and year. They are looked up in the indexes; one that is not found is dropped, so do \
not guess.

JSON: {{"queries": [{{"q": "...", "aim": "foundational|turning point|survey|recent|minority|adjacent"}}], \
"landmarks": [{{"title": "...", "author": "...", "year": 2020}}]}}"""
    out = await llm.ask(b.access, "queries", body)
    queries = [q for q in out.get("queries") or [] if isinstance(q, dict) and q.get("q")][:12]
    found: dict[str, dict[str, Any]] = {}
    landmarks: set[str] = set()
    for pid in seeds or []:
        if e := _entry(pid):
            found[e["paper_id"]] = e
    from aurora.literature.check import Reference
    for lm in [x for x in out.get("landmarks") or [] if isinstance(x, dict) and x.get("title")][:12]:
        try:
            year = int(lm["year"]) if str(lm.get("year", "")).isdigit() else None
            res = await b.lit.check_reference(Reference(title=str(lm["title"]), authors=str(lm.get("author", "")), year=year))
        except Exception:
            continue
        if res.verified and res.paper is not None:
            e = library.upsert_paper(res.paper)
            found.setdefault(e["paper_id"], e)
            landmarks.add(e["paper_id"])
    b.log("papers", f"{len(landmarks)} landmark papers named by the model and found in the indexes")
    for q in queries:
        try:
            hits = await b.lit.search(str(q["q"]), by="Field map", context=f"Map · {b.topic[:40]}", limit=8)
        except Exception as exc:
            b.log("papers", f"search “{q['q']}” failed: {exc}")
            continue
        for e in hits:
            found.setdefault(e["paper_id"], e)
        b.log("papers", f"searched “{q['q']}”: {len(hits)} checked papers")
    candidates = [e for e in found.values() if e["paper"].title]
    if not candidates:
        raise llm.ModelError("The searches found no checked papers; the indexes may be unreachable")

    def line(e: dict[str, Any]) -> str:
        p = e["paper"]
        cites = f", cited {p.cited_by} times" if p.cited_by else ""
        mark = " [landmark]" if e["paper_id"] in landmarks else ""
        return _brief_line({"key": e["key"], "paper": p}, 50).replace("\n", f"{cites}{mark}\n", 1)

    lines = "\n".join(line(e) for e in sorted(candidates, key=lambda e: e["paper"].year or 0))
    years = sorted(e["paper"].year for e in candidates if e["paper"].year)
    early = years[len(years) // 3] if years else 0
    body = f"""Research direction: {b.topic}
Capabilities a solution needs:
{_capabilities(b.content)}

Candidate papers (all checked against the indexes):
{lines}

Choose the papers for a map of this direction: up to {b.size} "core" papers that address it directly, and up to 5 \
"adjacent" papers from other fields with a structurally similar problem. The core must cover the whole history: \
the foundational papers and turning points (keep the landmarks unless they are off topic; at least a third of the \
core from before {early or 'the recent years'} when such candidates exist), high-impact recent work, and minority \
approaches. Benchmarks and datasets are not methods: give them the role "benchmark" (at most four); they are \
evidence of what the field measures. Leave out surveys from the core (keep at most two as "survey"), and anything \
off topic.

JSON: {{"keep": [{{"key": "...", "role": "core|benchmark|adjacent|survey", "why": "..."}}]}}"""
    out = await llm.ask(b.access, "select", body)
    keys = {e["key"]: e for e in candidates}
    kept = [(k, str(x.get("role", "core"))) for x in out.get("keep") or [] if isinstance(x, dict)
            and (k := str(x.get("key", "")).strip("[] ")) in keys]
    core = [keys[k] for k, role in kept if role == "core"][: b.size]
    if seeds:          # the papers the person gave are always in
        core += [e for pid, e in found.items() if pid in seeds and e not in core]
    # held out: the most recent papers and a spread of the rest, kept out of building the framework
    by_year = sorted(core, key=lambda e: -(e["paper"].year or 0))
    n_held = min(HELD_OUT, max(2, round(len(core) * 0.15))) if len(core) >= 6 else 0
    rest = by_year[2:]
    held = (by_year[:min(2, n_held)] + rest[1::max(2, len(rest) // max(1, n_held - 2))][: max(0, n_held - 2)]) if n_held else []
    for e in core:
        review_store.add_paper(b.map_id, e["paper_id"], "held_out" if e in held else "core")
    for k, role in kept:
        if role in ("adjacent", "survey", "benchmark") and keys[k] not in core:
            review_store.add_paper(b.map_id, keys[k]["paper_id"], role)
    b.content["queries"] = queries
    b.log("papers", f"{len(core)} papers in the map, {len(held)} of them held out to test it")


async def _paper_text(b: Build, pid: str) -> tuple[str, str]:
    """(text to read, where it came from): the PDF's start and its limitations or conclusion, else the abstract."""
    from aurora.literature import reading
    e = _entry(pid)
    abstract = e["paper"].abstract if e else ""
    path = reading.find_pdf(pid, b.owner, b.campaign_id) or await reading.fetch_open_pdf(pid)
    if path is None:
        return abstract, "abstract"
    pages = reading.page_texts(path)
    if not any(p.strip() for p in pages):
        return abstract, "abstract"
    head = "\n".join(pages[:2])
    tail = ""
    for i in range(len(pages) // 2, len(pages)):
        if re.search(r"\b(limitations?|conclusions?|discussion)\b", pages[i], re.I):
            tail = "\n".join(pages[i:i + 2])
            break
    text = (head[: CARD_TEXT * 2 // 3] + "\n…\n" + tail[: CARD_TEXT // 3]).strip()
    return text, "pdf"


async def card_for(b: Build, pid: str) -> dict[str, Any]:
    e = _entry(pid)
    if e is None:
        return {}
    text, source = await _paper_text(b, pid)
    term = (b.content.get("need") or {}).get("term") or b.topic
    body = f"""Paper [{e['key']}]: {e['paper'].title} ({e['paper'].year or 'n.d.'}, {e['paper'].venue or 'venue unknown'})
Capabilities of the direction "{b.topic}":
{_capabilities(b.content)}

Text of the paper ({'its first pages and its limitations or conclusion' if source == 'pdf' else 'its abstract only'}):
{text or '(no text)'}

Extract a card, from the text only:
- "sub_problem": the specific problem it addresses, one sentence.
- "choice": its core design decision, one sentence.
- "assumptions": what it relies on (two to four).
- "baselines": what it compares against.
- "limitations": each with "stated": true when the paper says it, false when you infer it.
- "results": its main claims, each with the benchmark or setting.
- "capabilities": ids of the capabilities above it addresses.
- "term_use": how this paper uses the word "{term}" (or what it calls the same thing), one sentence.
- "quotes": up to three short passages copied word for word from the text, each tied to the field it supports.

JSON: {{"sub_problem": "...", "choice": "...", "assumptions": ["..."], "baselines": ["..."], \
"limitations": [{{"text": "...", "stated": true}}], "results": [{{"claim": "...", "benchmark": "..."}}], \
"capabilities": ["c1"], "term_use": "...", "quotes": [{{"field": "choice", "text": "..."}}]}}"""
    out = await llm.ask(b.access, "card", body, max_tokens=3000)
    caps = {c["id"] for c in (b.content.get("need") or {}).get("capabilities") or []}
    quotes = [{"field": str(q.get("field", "")), "text": " ".join(str(q.get("text", "")).split())}
              for q in out.get("quotes") or [] if isinstance(q, dict)]
    return {
        "sub_problem": str(out.get("sub_problem", "")), "choice": str(out.get("choice", "")),
        "assumptions": [str(x) for x in out.get("assumptions") or []][:5],
        "baselines": [str(x) for x in out.get("baselines") or []][:6],
        "limitations": [{"text": str(x.get("text", "")), "stated": bool(x.get("stated"))}
                        for x in out.get("limitations") or [] if isinstance(x, dict) and x.get("text")][:6],
        "results": [{"claim": str(x.get("claim", "")), "benchmark": str(x.get("benchmark", ""))}
                    for x in out.get("results") or [] if isinstance(x, dict) and x.get("claim")][:6],
        "capabilities": [c for c in out.get("capabilities") or [] if c in caps],
        "term_use": str(out.get("term_use", "")),
        # a quote the text does not contain is kept out: evidence must be the paper's own words
        "quotes": [q for q in quotes if _verified(q["text"], text)][:3],
        "source": source,
    }


async def step_cards(b: Build) -> None:
    rows = [r for r in review_store.papers(b.map_id) if r["role"] in ("core", "held_out", "joined", "adjacent", "benchmark")
            and not r["card"]]
    cards = await llm.gather_limited([card_for(b, r["paper_id"]) for r in rows], limit=6)
    if rows:
        llm.settled(cards, "paper card")
    done = 0
    for r, card in zip(rows, cards):
        if isinstance(card, Exception) or not card:
            b.log("cards", f"[{key_of(r['paper_id'])}]: could not be read ({card})")
            continue
        review_store.set_card(b.map_id, r["paper_id"], card)
        done += 1
    pdfs = sum(1 for r in review_store.papers(b.map_id) if r["card"].get("source") == "pdf")
    b.log("cards", f"{done} cards; {pdfs} from the PDF, the rest from the abstract")


# ---------------------------------------------------------------------------
# 1. Definitions


async def step_definition(b: Build) -> None:
    rows = _papers(b.map_id, ("core", "joined", "adjacent"))
    uses = "\n".join(f"[{r['key']}] {r['card'].get('term_use', '')}" for r in rows if r["card"].get("term_use"))
    term = (b.content.get("need") or {}).get("term") or b.topic
    decided = b.content.get("decided") or []
    decided_text = ("Boundary decisions the researcher has made, which the definition must follow:\n"
                    + "\n".join("- " + d for d in decided)) if decided else ""
    body = f"""Research direction: {b.topic}. The concept studied: "{term}".
Capabilities a solution needs (from the tasks):
{_capabilities(b.content)}

How the papers use the term:
{uses or '(no papers)'}
{decided_text}

1. "communities": the different research communities that use the term, what each means by it, and example papers \
(keys above). Label each with "lit" or "inf".
2. "terms": the same word used for different things, and different words used for the same thing.
3. "conflicts": questions the communities disagree on (e.g. "is X a kind of {term}?"), each with the positions and who holds them.
4. "working": a working definition, as one sentence of at most 40 words. It says what counts as {term} (descriptive), \
not what good {term} should achieve: the capabilities are the yardstick, the definition only has to let every one \
of them be measured. Its "components" (three to five) are the parts every instance of {term} has, so that every \
paper takes some value on each (for example what is kept, how it is written, who reads it, how long it lasts), each \
with an id k1, k2, ..., a name and the words of the sentence it comes from. Its "boundaries": each a question, the \
decision, why, and what it puts in or out. And "covers": how it lets each capability be measured.

JSON: {{"communities": [{{"community": "...", "meaning": "...", "examples": ["Key"], "label": "lit|inf"}}], \
"terms": [{{"term": "...", "meanings": ["..."]}}], \
"conflicts": [{{"question": "...", "positions": [{{"view": "...", "who": ["Key"]}}]}}], \
"working": {{"text": "...", "components": [{{"id": "k1", "name": "...", "phrase": "..."}}], \
"boundaries": [{{"question": "...", "decision": "...", "why": "...", "consequence": "..."}}], \
"covers": [{{"capability": "c1", "how": "..."}}]}}}}"""
    out = await llm.ask(b.access, "definition", body)
    working = out.get("working") or {}
    comps = [{"id": f"k{i + 1}", "name": str(c.get("name", "")), "phrase": str(c.get("phrase", ""))}
             for i, c in enumerate(working.get("components") or []) if isinstance(c, dict) and c.get("name")]
    b.content["definition"] = {
        "communities": [c for c in out.get("communities") or [] if isinstance(c, dict)],
        "terms": [t for t in out.get("terms") or [] if isinstance(t, dict)],
        "conflicts": [c for c in out.get("conflicts") or [] if isinstance(c, dict)],
        "working": {"text": str(working.get("text", "")), "components": comps,
                    "boundaries": [x for x in working.get("boundaries") or [] if isinstance(x, dict)],
                    "covers": [x for x in working.get("covers") or [] if isinstance(x, dict)]},
    }
    b.log("definition", f"{len(b.content['definition']['communities'])} ways the field uses “{term}”; "
                        f"a working definition in {len(comps)} parts")


# ---------------------------------------------------------------------------
# 2–3. The design space, placing the papers, testing on held-out ones


def _definition_text(content: dict[str, Any]) -> str:
    w = (content.get("definition") or {}).get("working") or {}
    comps = "\n".join(f"- {c['id']}: {c['name']} (“{c['phrase']}”)" for c in w.get("components") or [])
    return f"Working definition: {w.get('text', '')}\nIts components:\n{comps}"


async def propose_framework(b: Build, model: str, n: int, revision: str = "", current: dict[str, Any] | None = None) -> dict[str, Any]:
    rows = _papers(b.map_id)
    cards = "\n".join(_card_line(r) for r in rows)
    revise = ""
    if revision and current:
        dims = "\n".join(f"- {d['id']} {d['name']}: " + ", ".join(v["label"] for v in d["values"]) for d in current["dims"])
        revise = f"\nThe current framework:\n{dims}\nRevise it as follows, changing as little else as possible: {revision}\n"
    body = f"""Research direction: {b.topic}
{_definition_text(b.content)}
Capabilities a solution needs:
{_capabilities(b.content)}

The papers (problem and core design choice):
{cards}
{revise}
Propose the design space every paper is making choices in: two to four dimensions, as independent of each other as \
possible. Each dimension is one component of the definition (give its id), has two to five values that together \
cover every paper (include a value such as "none" or "not handled" when papers lack the part), and must explain \
differences between papers in success or cost on the capabilities (say how). Avoid surface labels (the backbone, \
the task, the venue).

JSON: {{"name": "a short name for the framework", "dims": [{{"id": "d1", "name": "...", "component": "k1", \
"values": [{{"id": "a", "label": "...", "meaning": "..."}}], "explains": "..."}}]}}"""
    out = await llm.ask(b.access.with_model(model), "framework", body, temperature=0.5)
    dims = []
    for i, d in enumerate(out.get("dims") or []):
        if not isinstance(d, dict) or not d.get("values"):
            continue
        values = [{"id": chr(97 + j), "label": str(v.get("label", "")), "meaning": str(v.get("meaning", ""))}
                  for j, v in enumerate(d["values"]) if isinstance(v, dict) and v.get("label")][:6]
        dims.append({"id": f"d{i + 1}", "name": str(d.get("name", "")), "component": str(d.get("component", "")),
                     "values": values, "explains": str(d.get("explains", ""))})
    return {"id": f"f{n}", "name": str(out.get("name", f"Framework {n}")), "by": model, "dims": dims[:4],
            "placements": {}, "held": {}, "scores": {}}


async def place(b: Build, fw: dict[str, Any], rows: list[dict[str, Any]], *, note: dict[str, str] | None = None) -> dict[str, dict[str, Any]]:
    """Place papers in a framework; ``{paper_id: {values, strain, note, evidence, verified}}``."""
    dims = "\n".join(f"- {d['id']} {d['name']}: " + "; ".join(f"{v['id']} = {v['label']} ({v['meaning']})" for v in d["values"])
                     for d in fw["dims"])
    out: dict[str, dict[str, Any]] = {}
    for start in range(0, len(rows), 12):
        batch = rows[start:start + 12]
        papers = "\n\n".join(
            _card_line(r) + "".join(f"\n  quote: «{q['text']}»" for q in (r["card"].get("quotes") or []))
            + (f"\n  abstract: {' '.join(r['paper'].abstract.split()[:90])}" if r["paper"].abstract else "")
            + (f"\n  the researcher says: {note[r['paper_id']]}" if note and r["paper_id"] in note else "")
            for r in batch)
        body = f"""The design space of "{b.topic}":
{dims}

Place each paper below: the value it takes on every dimension. If a paper fits a dimension only with a strained \
reading, or no value describes it, set "strain": true and say why in "note". Give as "evidence" a few words copied \
from its card, quote or abstract that justify the placement.

{papers}

JSON: {{"placements": [{{"key": "...", "values": {{"d1": "a"}}, "strain": false, "note": "...", "evidence": "..."}}]}}"""
        got = await llm.ask(b.access, "place", body, temperature=0.1)
        keys = {r["key"]: r for r in batch}
        for p in got.get("placements") or []:
            if not isinstance(p, dict) or (k := str(p.get("key", "")).strip("[] ")) not in keys:
                continue
            r = keys[k]
            valid = {d["id"]: {v["id"] for v in d["values"]} for d in fw["dims"]}
            values = {d: str(v) for d, v in (p.get("values") or {}).items() if d in valid and str(v) in valid[d]}
            missing = [d for d in valid if d not in values]
            source = " ".join([r["paper"].abstract or "", r["card"].get("sub_problem", ""), r["card"].get("choice", ""),
                               *[q["text"] for q in r["card"].get("quotes") or []]])
            evidence = " ".join(str(p.get("evidence", "")).split())
            out[r["paper_id"]] = {"values": values, "strain": bool(p.get("strain")) or bool(missing),
                                  "note": str(p.get("note", "")) or (f"No value fits on {', '.join(missing)}" if missing else ""),
                                  "evidence": evidence, "verified": _verified(evidence, source)}
    return out


def _nmi(a: list[str], b: list[str]) -> float:
    """Normalised mutual information of two placements (0: independent, 1: one decides the other)."""
    n = len(a)
    if n < 2:
        return 0.0
    ca, cb, cab = Counter(a), Counter(b), Counter(zip(a, b))
    h = lambda c: -sum((v / n) * math.log(v / n) for v in c.values())  # noqa: E731
    ha, hb = h(ca), h(cb)
    if ha == 0 or hb == 0:
        return 0.0
    mi = sum((v / n) * math.log((v / n) / ((ca[x] / n) * (cb[y] / n))) for (x, y), v in cab.items())
    return max(0.0, min(1.0, mi / math.sqrt(ha * hb)))


def score(fw: dict[str, Any]) -> dict[str, Any]:
    """The computable scores: share of papers that fit, of held-out papers that fit, independence of the dimensions."""
    pl, held = fw["placements"], fw["held"]
    fit = sum(not p["strain"] for p in pl.values()) / len(pl) if pl else 0.0
    held_fit = sum(not p["strain"] for p in held.values()) / len(held) if held else 0.0
    dims = [d["id"] for d in fw["dims"]]
    pairs = [(x, y) for i, x in enumerate(dims) for y in dims[i + 1:]]
    full = [p["values"] for p in pl.values() if all(d in p["values"] for d in dims)]
    nmi = [_nmi([v[x] for v in full], [v[y] for v in full]) for x, y in pairs]
    orth = 1 - (sum(nmi) / len(nmi)) if nmi else 0.0
    return {**fw.get("scores", {}), "fit": round(fit, 3), "held": round(held_fit, 3), "orth": round(orth, 3),
            "n_fit": f"{sum(not p['strain'] for p in pl.values())}/{len(pl)}",
            "n_held": f"{sum(not p['strain'] for p in held.values())}/{len(held)}"}


async def judge(b: Build, fw: dict[str, Any]) -> dict[str, Any]:
    """How well the dimensions explain differences in success and cost (a judgement, so labelled as such)."""
    rows = _papers(b.map_id)
    lines = []
    for r in rows:
        p = fw["placements"].get(r["paper_id"])
        if not p:
            continue
        vals = ", ".join(f"{d['name']}={next((v['label'] for v in d['values'] if v['id'] == p['values'].get(d['id'])), '?')}"
                         for d in fw["dims"])
        res = "; ".join(x["claim"] for x in r["card"].get("results") or [])[:300]
        lim = "; ".join(x["text"] for x in r["card"].get("limitations") or [])[:300]
        lines.append(f"[{r['key']}] {vals}\n  results: {res}\n  limitations: {lim}")
    body = f"""A framework for "{b.topic}" and where it puts each paper, with the papers' results and limitations:
{chr(10).join(lines)}

How well do the positions explain the differences in what the papers achieve, what they cost, and where they fail? \
Score 0 (positions say nothing about outcomes) to 1 (positions predict outcomes well), and give the reason.

JSON: {{"score": 0.0, "reason": "..."}}"""
    out = await llm.ask(b.access, "judge", body, temperature=0)
    try:
        s = max(0.0, min(1.0, float(out.get("score", 0))))
    except (TypeError, ValueError):
        s = 0.0
    return {"disc": round(s, 3), "disc_reason": str(out.get("reason", ""))}


def total(s: dict[str, Any]) -> float:
    return 0.3 * s.get("fit", 0) + 0.3 * s.get("held", 0) + 0.25 * s.get("disc", 0) + 0.15 * s.get("orth", 0)


async def step_frameworks(b: Build) -> None:
    models = list(dict.fromkeys(b.seat_models or [b.access.model]))[:3]
    while len(models) < 3:
        models.append(b.access.model)
    proposals = llm.settled(await llm.gather_limited([propose_framework(b, m, i + 1) for i, m in enumerate(models)], limit=3),
                            "framework proposal")
    fws = [f for f in proposals if f["dims"]]
    if not fws:
        raise llm.ModelError("No model proposed a usable framework")
    core, held = _papers(b.map_id), _papers(b.map_id, ("held_out",))

    async def evaluate(fw: dict[str, Any]) -> None:
        fw["placements"] = await place(b, fw, core)
        fw["held"] = await place(b, fw, held) if held else {}     # placed apart: it sees no other placement
        fw["scores"] = score(fw)
        fw["scores"].update(await judge(b, fw))

    evaluated = await llm.gather_limited([evaluate(fw) for fw in fws], limit=3)
    fws = [fw for fw, res in zip(fws, evaluated) if not isinstance(res, BaseException) and fw["placements"]]
    if not fws:
        raise llm.ModelError("No framework could be scored: placing the papers failed")
    fws.sort(key=lambda f: -total(f["scores"]))
    b.content["frameworks"] = fws
    b.content["chosen"] = fws[0]["id"]
    _proposals_from_misfits(b.content)
    b.log("frameworks", f"{len(fws)} frameworks scored; chose “{fws[0]['name']}” "
                        f"({fws[0]['scores']['n_fit']} papers fit, {fws[0]['scores']['n_held']} held out)")


def chosen(content: dict[str, Any]) -> dict[str, Any] | None:
    return next((f for f in content.get("frameworks") or [] if f["id"] == content.get("chosen")), None)


def _proposals_from_misfits(content: dict[str, Any]) -> None:
    """Held-out (or newly joined) papers that do not fit: each a proposal to revise the framework."""
    fw = chosen(content)
    if fw is None:
        return
    props = [p for p in content.get("proposals") or [] if p["state"] != "pending" or p.get("framework") != fw["id"]]
    for pid, p in {**fw["held"], **fw.get("joined", {})}.items():
        if p["strain"]:
            props.append({"id": f"p{len(props) + 1}_{pid[-4:]}", "kind": "framework", "framework": fw["id"], "paper_id": pid,
                          "text": p["note"], "state": "pending", "at": time.time()})
    content["proposals"] = props


# ---------------------------------------------------------------------------
# 4. How well each capability is solved


async def progress_for(b: Build, cap: dict[str, Any]) -> dict[str, Any]:
    rows = [r for r in _papers(b.map_id, ("core", "held_out", "joined", "benchmark"))
            if cap["id"] in (r["card"].get("capabilities") or [])]
    lines = []
    for r in rows:
        if r["role"] == "benchmark":
            lines.append(f"[{r['key']}] (a benchmark, {r['paper'].year}) {r['card'].get('choice', '')}\n"
                         f"  what it shows: {'; '.join(x['claim'] for x in r['card'].get('results') or [])}")
            continue
        c = r["card"]
        lines.append(f"[{r['key']}] ({r['paper'].year}) {c.get('choice', '')}\n"
                     f"  results: {'; '.join(x['claim'] + (' (' + x['benchmark'] + ')' if x['benchmark'] else '') for x in c.get('results') or [])}\n"
                     f"  limitations: {'; '.join(x['text'] + ('' if x['stated'] else ' (inferred)') for x in c.get('limitations') or [])}\n"
                     f"  relies on: {'; '.join(c.get('assumptions') or [])}")
    body = f"""Capability of "{b.topic}": {cap['name']}. Needs: {cap['info']} ({cap['timescale']}). Example: {cap['example']}

The papers that address it:
{chr(10).join(lines) or '(none in the map)'}

How well is it solved? Judge from the evidence above only.
- "status": "solved", "partly", "early" or "open"; "headline": one sentence a reader can take away.
- "best": the keys of the strongest work; "results": the best results, each with its paper and benchmark.
- "conditions": the conditions under which the results hold (static scenes, simulation, short horizons...).
- "limitations": limitations that recur across papers, each with the keys of the papers that state it.
- "conflicts": results that disagree, if any. "cost": what it costs to do. "eval_gap": where evaluation and the \
real need part, if they do.

JSON: {{"status": "...", "headline": "...", "best": ["Key"], "results": [{{"key": "...", "claim": "...", "benchmark": "..."}}], \
"conditions": {PARAGRAPHS}, "limitations": [{{"text": "...", "keys": ["Key"]}}], "conflicts": {PARAGRAPHS}, \
"cost": {PARAGRAPHS}, "eval_gap": {PARAGRAPHS}}}"""
    out = await llm.ask(b.access, "progress", body)
    status = str(out.get("status", "open")).lower()
    return {"capability": cap["id"], "status": status if status in STATUSES else "open", "headline": str(out.get("headline", "")),
            "best": [str(k).strip("[] ") for k in out.get("best") or []][:4],
            "results": [x for x in out.get("results") or [] if isinstance(x, dict)][:5],
            "conditions": paragraphs(out.get("conditions") or []),
            "limitations": sorted([x for x in out.get("limitations") or [] if isinstance(x, dict) and x.get("text")],
                                  key=lambda x: -len(x.get("keys") or [])),
            "conflicts": paragraphs(out.get("conflicts") or []), "cost": paragraphs(out.get("cost") or []),
            "eval_gap": paragraphs(out.get("eval_gap") or []), "papers": [r["key"] for r in rows]}


async def step_progress(b: Build) -> None:
    caps = (b.content.get("need") or {}).get("capabilities") or []
    rows = await llm.gather_limited([progress_for(b, c) for c in caps], limit=4)
    b.content["progress"] = llm.settled(rows, "judgement of progress")
    if len(b.content["progress"]) < len(caps):
        b.log("progress", f"{len(caps) - len(b.content['progress'])} capabilities could not be judged; run again from this step")
    states = Counter(r["status"] for r in b.content["progress"])
    b.log("progress", ", ".join(f"{n} {s}" for s, n in states.items()) or "no capability judged")


# ---------------------------------------------------------------------------
# 5. Trends: the timeline, and how papers move through the design space


def periods(years: list[int]) -> list[tuple[int, int]]:
    """Three periods with about as many papers each."""
    ys = sorted(y for y in years if y)
    if not ys:
        return []
    if len(set(ys)) < 3:
        return [(ys[0], ys[-1])]
    cut1, cut2 = ys[len(ys) // 3], ys[2 * len(ys) // 3]
    out = [(ys[0], cut1 - 1 if cut1 > ys[0] else cut1), (cut1 if cut1 > ys[0] else cut1 + 1, cut2 - 1), (cut2, ys[-1])]
    return [(a, b) for a, b in out if a <= b]


def distribution(b_map_id: str, content: dict[str, Any]) -> dict[str, Any]:
    """For each period, how many papers take each value of each dimension (and which)."""
    fw = chosen(content)
    if fw is None:
        return {}
    rows = _papers(b_map_id, ("core", "held_out", "joined"))
    allp = {**fw["placements"], **fw["held"], **fw.get("joined", {})}
    years = {r["paper_id"]: r["paper"].year or 0 for r in rows}
    per = periods(list(years.values()))
    out = []
    for lo, hi in per:
        dims = {}
        for d in fw["dims"]:
            dims[d["id"]] = {v["id"]: [key_of(pid) for pid, p in allp.items() if lo <= years.get(pid, 0) <= hi
                                      and p["values"].get(d["id"]) == v["id"]] for v in d["values"]}
        out.append({"from": lo, "to": hi, "dims": dims})
    return {"periods": out}


def sparse_regions(map_id: str, content: dict[str, Any]) -> list[dict[str, Any]]:
    """Pairs of values (on two dimensions) that at most one paper takes: where untried approaches may be."""
    fw = chosen(content)
    if fw is None:
        return []
    allp = list({**fw["placements"], **fw["held"], **fw.get("joined", {})}.items())
    out = []
    for i, d1 in enumerate(fw["dims"]):
        for d2 in fw["dims"][i + 1:]:
            for v1 in d1["values"]:
                for v2 in d2["values"]:
                    who = [key_of(pid) for pid, p in allp if p["values"].get(d1["id"]) == v1["id"] and p["values"].get(d2["id"]) == v2["id"]]
                    if len(who) <= 1:
                        out.append({"a": f"{d1['name']}: {v1['label']}", "b": f"{d2['name']}: {v2['label']}", "papers": who})
    return out


async def step_trends(b: Build) -> None:
    fw = chosen(b.content)
    rows = sorted(_papers(b.map_id, ("core", "held_out", "joined")), key=lambda r: r["paper"].year or 0)
    allp = {**fw["placements"], **fw["held"], **fw.get("joined", {})} if fw else {}
    dimname = {d["id"]: d for d in fw["dims"]} if fw else {}

    def where(pid: str) -> str:
        p = allp.get(pid)
        if not p:
            return ""
        return "; ".join(f"{dimname[d]['name']}={next((v['label'] for v in dimname[d]['values'] if v['id'] == x), x)}"
                         for d, x in p["values"].items() if d in dimname)

    lines = "\n".join(f"[{r['key']}] {r['paper'].year}: {r['card'].get('choice', '')} | {where(r['paper_id'])}" for r in rows)
    body = f"""Research direction: {b.topic}
The papers in time order, with their core choice and their place in the design space:
{lines}

1. "timeline": three to six phases, each with its years, a name, what drove the turn into it (one of: new data, new \
compute, a failure mode found, a tool imported from an adjacent field, a new evaluation, the problem redefined), a \
paragraph on how, and its turning-point papers.
2. "trends": two to four movements through the design space: where recent papers concentrate, which constraint is \
being relaxed, whether the forces behind it will last, and where the next bottleneck is likely. Each with its \
label ("inf" or "spec"), the strength of the evidence ("strong", "moderate", "weak") and what it is based on.

JSON: {{"timeline": [{{"years": "...", "name": "...", "driver": "...", "text": "...", "turning": ["Key"]}}], \
"trends": [{{"title": "...", "text": "...", "label": "inf|spec", "strength": "...", "based": "..."}}]}}"""
    out = await llm.ask(b.access, "trends", body)
    b.content["timeline"] = [t for t in out.get("timeline") or [] if isinstance(t, dict)]
    b.content["trends"] = [{**t, "label": t.get("label") if t.get("label") in ("inf", "spec") else "spec"}
                           for t in out.get("trends") or [] if isinstance(t, dict)]
    b.log("trends", f"{len(b.content['timeline'])} phases, {len(b.content['trends'])} trends")


# ---------------------------------------------------------------------------
# 6. Assumptions the field shares


async def step_assumptions(b: Build) -> None:
    rows = _papers(b.map_id, ("core", "held_out", "joined"))
    lines = "\n".join(f"[{r['key']}] relies on: {'; '.join(r['card'].get('assumptions') or [])}" for r in rows)
    bounds = "\n".join(f"- {x.get('question')}: {x.get('decision')}" for x in
                       ((b.content.get("definition") or {}).get("working") or {}).get("boundaries") or [])
    body = f"""Research direction: {b.topic}
What each paper relies on:
{lines}
The definition's boundary decisions:
{bounds}

Find two to four assumptions nearly every paper accepts without saying so. For each: the papers that rely on it, why \
people accept it (history, evidence or convenience), what change of conditions or evidence would overturn it, which \
papers already challenge it, and the label of the claim ("inf" or "lit").

JSON: {{"assumptions": [{{"text": "...", "keys": ["Key"], "why": "...", "overturn": "...", "challengers": ["Key"], "label": "inf|lit"}}]}}"""
    out = await llm.ask(b.access, "assumptions", body)
    b.content["assumptions"] = [a for a in out.get("assumptions") or [] if isinstance(a, dict) and a.get("text")]
    b.log("assumptions", f"{len(b.content['assumptions'])} shared assumptions")


# ---------------------------------------------------------------------------
# 7. Problems that are attempted but not well solved


async def step_problems(b: Build) -> None:
    caps = {c["id"]: c for c in (b.content.get("need") or {}).get("capabilities") or []}
    prog = "\n".join(
        f"- {p['capability']} {caps.get(p['capability'], {}).get('name', '')}: {p['status']}. {p['headline']} "
        f"Recurring limitations: {'; '.join(x['text'] + ' (' + _keylist(x.get('keys') or []) + ')' for x in p['limitations'][:3])}"
        for p in b.content.get("progress") or [])
    assumptions = "\n".join(f"- {a['text']}" for a in b.content.get("assumptions") or [])
    sparse = "\n".join(f"- {s['a']} × {s['b']}: {len(s['papers'])} paper(s)" for s in sparse_regions(b.map_id, b.content)[:25])
    adjacent = "\n".join(_brief_line(r, 50) for r in _papers(b.map_id, ("adjacent",)))
    body = f"""Research direction: {b.topic}
How well each capability is solved:
{prog}
Assumptions the field shares:
{assumptions}
Sparse regions of the design space (value pairs at most one paper takes):
{sparse or '(none)'}
Adjacent-field papers in the map:
{adjacent or '(none)'}

Find three to five problems that people have attempted but not solved well. A good one is important (it serves a \
capability that matters) and far from solved, with evidence: a result that plateaus, conditions that do not hold \
in practice, a limitation many papers admit, conflicting results, a prohibitive cost, or a gap between evaluation \
and need. An empty region nobody tried is not a problem by itself; use the sparse regions only as possible approaches.

For each problem:
- "title", "capability" (id), "importance" (high, mid, low) and "gap": why it is not well solved, with "evidence" keys.
- "diagnosis": the root cause ("cause": one of signal, data, representation, evaluation, compute, assumption, \
definition) and a paragraph on the mechanism.
- "why_now": what changed that makes it tractable now.
- "approaches": two or three, each saying where it comes from ("design space", "adjacent field", "dropped assumption") \
and labelled "inf" or "spec".
- "first_experiment": the smallest experiment that tells whether to continue. "failure": the most likely reason it fails.
- "search": a search query of three to six words that would find the closest existing work.
Also "adjacent": two or three adjacent fields with a structurally similar problem: what is similar, what to borrow, \
and "checks" whether the conditions that make it work there hold here (ok true, false, or null when partly).

JSON: {{"problems": [{{"title": "...", "capability": "c1", "importance": "high", "gap": "...", "evidence": ["Key"], \
"diagnosis": {{"cause": "...", "text": "..."}}, "why_now": "...", "approaches": [{{"text": "...", "from": "...", "label": "inf"}}], \
"first_experiment": "...", "failure": "...", "search": "..."}}], \
"adjacent": [{{"field": "...", "same": "...", "borrow": "...", "checks": [{{"ok": true, "text": "..."}}]}}]}}"""
    out = await llm.ask(b.access, "problems", body)
    problems = []
    for i, p in enumerate(out.get("problems") or []):
        if not isinstance(p, dict) or not p.get("title"):
            continue
        diag = p.get("diagnosis") or {}
        cause = str(diag.get("cause", "")).lower()
        problems.append({**p, "id": f"q{i + 1}", "diagnosis": {"cause": cause if cause in CAUSES else "assumption",
                                                                 "text": str(diag.get("text", ""))},
                         "closest": [], "novelty": "", "novelty_note": ""})
    b.content["problems"] = problems
    b.content["adjacent"] = [a for a in out.get("adjacent") or [] if isinstance(a, dict)]
    b.content["sparse"] = sparse_regions(b.map_id, b.content)
    # the closest existing work, found by searching, never declared from memory
    await llm.gather_limited([closest_work(b, p) for p in problems], limit=3)
    b.log("problems", f"{len(problems)} problems not well solved, each checked against a search for the closest work")


async def closest_work(b: Build, p: dict[str, Any]) -> None:
    hits: list[dict[str, Any]] = []
    query = str(p.get("search") or p["title"])
    for q in dict.fromkeys(x for x in (query, " ".join(query.split()[:6]), " ".join(str(p["title"]).split()[:8])) if x.strip()):
        try:
            hits = await b.lit.search(q, by="Field map", context=f"Closest work · {p['title'][:40]}", limit=6)
        except Exception as exc:
            p["novelty_note"] = f"The search failed: {exc}"
            return
        if hits:
            break
    if not hits:
        p["novelty"], p["novelty_note"] = "unknown", "The search found nothing; that is not proof nobody did it."
        return
    lines = "\n".join(_brief_line({"key": e["key"], "paper": e["paper"]}, 70) for e in hits)
    body = f"""A research problem and an approach to it:
{p['title']}. {p.get('gap', '')}
Approaches: {'; '.join(a.get('text', '') for a in p.get('approaches') or [] if isinstance(a, dict))}

Papers a search found:
{lines}

Which are closest, and how does the problem or its approaches differ from each? Is the direction new ("yes"), \
partly done ("partly") or done ("no")?

JSON: {{"closest": [{{"key": "...", "difference": "..."}}], "novel": "yes|partly|no", "note": "..."}}"""
    out = await llm.ask(b.access, "closest", body, temperature=0)
    keys = {e["key"] for e in hits}
    p["closest"] = [c for c in out.get("closest") or [] if isinstance(c, dict) and str(c.get("key", "")).strip("[] ") in keys][:4]
    novel = str(out.get("novel", "")).lower()
    p["novelty"] = novel if novel in ("yes", "partly", "no") else "unknown"
    p["novelty_note"] = str(out.get("note", ""))


# ---------------------------------------------------------------------------
# 8. What to read next


def _keylist(keys: list[str]) -> str:
    return ", ".join(f"[{k}]" for k in keys if k) or "none"


async def step_read_next(b: Build) -> None:
    turning = sorted({k.strip("[] ") for t in b.content.get("timeline") or [] for k in t.get("turning") or []})
    fw = chosen(b.content)
    broke = [key_of(pid) for pid, p in {**(fw or {}).get("held", {}), **(fw or {}).get("joined", {})}.items() if p["strain"]]
    assumed = sorted({k for a in b.content.get("assumptions") or [] for k in (a.get("keys") or [])[:2]})
    closest = sorted({c.get("key", "") for p in b.content.get("problems") or [] for c in p.get("closest") or []})
    body = f"""Research direction: {b.topic}
Turning points: {_keylist(turning)}
Papers that did not fit the framework: {_keylist(broke)}
Papers behind the shared assumptions: {_keylist(assumed)}
Closest work to the open problems: {_keylist(closest)}

Recommend three to five papers to deep-read, those the map's judgements rest on most. For each, why, and which of \
the twelve deep-read questions to focus on (1 problem, 2 prior work, 3 the authors' path to the idea, 4 intuition, \
5 method, 6 mathematics, 7 experiments, 8 take-aways, 9 weakest assumption, 10 one-week reproduction, \
11 counterexample, 12 follow-up idea).

JSON: {{"read": [{{"key": "...", "why": "...", "focus": [3, 9]}}]}}"""
    out = await llm.ask(b.access, "read_next", body)
    keys = _by_key(b.map_id)
    for c in closest:            # the closest work is not in the map; it may still be recommended
        e = library.paper(c)
        if e:
            keys.setdefault(e["key"], e["paper_id"])
    b.content["read_next"] = [{"key": k, "paper_id": keys[k], "why": str(r.get("why", "")),
                               "focus": [int(x) for x in r.get("focus") or [] if str(x).isdigit() and 1 <= int(x) <= 12]}
                              for r in out.get("read") or [] if isinstance(r, dict) and (k := str(r.get("key", "")).strip("[] ")) in keys][:5]
    b.log("read_next", f"{len(b.content['read_next'])} papers to read closely")


# ---------------------------------------------------------------------------
# Running it


RUNNERS = {"need": step_need, "papers": step_papers, "cards": step_cards, "definition": step_definition,
           "frameworks": step_frameworks, "progress": step_progress, "trends": step_trends,
           "assumptions": step_assumptions, "problems": step_problems, "read_next": step_read_next}


async def run(b: Build, *, start: str = "need", until: str = "read_next", reason: str = "Built",
              seeds: list[str] | None = None, tell_steps: bool = True) -> None:
    """Run the steps from ``start`` to ``until``, saving after each, then keep the result as a version.

    ``tell_steps``: tell the conversation each step's result (a first build); a rebuild tells only the new version."""
    steps = STEPS[STEPS.index(start): STEPS.index(until) + 1]
    review_store.set_state(b.map_id, state="building", error="")
    try:
        for step in steps:
            review_store.set_state(b.map_id, step=step)
            b.log(step, f"{STEP_NAMES[step]}…")
            if step == "papers":
                await step_papers(b, seeds)
            else:
                await RUNNERS[step](b)
            b.content["done"] = sorted(set(b.content.get("done") or []) | {step}, key=STEPS.index)
            b.save()
            if tell_steps:
                b.tell(step)
        if b.ideas:
            try:
                await place_ideas(b, b.ideas)
            except Exception:
                logger.info("placing the ideas on map %s failed", b.map_id, exc_info=True)
    except Exception as exc:
        logger.warning("building map %s failed at %s", b.map_id, review_store.map(b.map_id)["step"], exc_info=True)
        b.save()
        review_store.set_state(b.map_id, state="failed", error=str(exc)[:500])
        b.log("error", f"Stopped: {str(exc)[:300]}")
        b.tell("failed")
        return
    review_store.commit(b.map_id, b.content, reason)
    review_store.set_state(b.map_id, state="ready", step="")
    b.log("done", f"Version {review_store.map(b.map_id)['version']}: {reason}")
    b.tell("version")


# ---------------------------------------------------------------------------
# The space's ideas on the map, and the map as the agents see it


async def place_ideas(b: Build, ideas: list[dict[str, Any]]) -> None:
    """Where each idea sits in the design space, and which unsolved problem it answers (if any)."""
    fw = chosen(b.content)
    if fw is None or not ideas:
        return
    dims = "\n".join(f"- {d['id']} {d['name']}: " + "; ".join(f"{v['id']} = {v['label']}" for v in d["values"])
                     for d in fw["dims"])
    probs = "\n".join(f"- {p['id']}: {p['title']}" for p in b.content.get("problems") or [])
    lines = "\n".join(f"- {i['card_id']}: {i['text']}" for i in ideas[:40])
    out = await llm.ask(b.access, "place_ideas", f"""The design space of "{b.topic}":
{dims}
Problems in it that are attempted but not well solved:
{probs or '(none)'}

A researcher's ideas:
{lines}

For each idea: the value it would take on each dimension (leave out a dimension it does not decide), the problem \
it answers (its id, or "" when none), and one sentence on where it sits relative to the papers.
JSON: {{"ideas": [{{"card_id": "...", "values": {{"d1": "a"}}, "problem": "q1", "note": "..."}}]}}""", temperature=0)
    known = {i["card_id"]: i for i in ideas}
    valid = {d["id"]: {v["id"] for v in d["values"]} for d in fw["dims"]}
    probs_ids = {p["id"] for p in b.content.get("problems") or []}
    placed = b.content.setdefault("ideas", {})
    for x in out.get("ideas") or []:
        if not isinstance(x, dict) or x.get("card_id") not in known:
            continue
        placed[x["card_id"]] = {
            "text": known[x["card_id"]]["text"][:300], "framework": fw["id"],
            "values": {d: str(v) for d, v in (x.get("values") or {}).items() if d in valid and str(v) in valid[d]},
            "problem": x.get("problem") if x.get("problem") in probs_ids else "", "note": str(x.get("note", ""))}


def summary_for_agents(m: dict[str, Any], max_chars: int = 5000) -> str:
    """The map, as the brainstorm's agents read it with every turn."""
    c = m["content"]
    lines = [f"## The map of the field: {m['topic']} (version {m['version']})"]
    w = ((c.get("definition") or {}).get("working") or {})
    if w.get("text"):
        lines.append(f"Working definition: {w['text']}")
    for d in c.get("decided") or []:
        lines.append(f"The researcher decided: {d}")
    caps = {x["id"]: x["name"] for x in (c.get("need") or {}).get("capabilities") or []}
    fw = chosen(c)
    if fw:
        lines.append("Design space: " + "; ".join(f"{d['name']} ({', '.join(v['label'] for v in d['values'])})" for d in fw["dims"]))
    if c.get("progress"):
        lines.append("How far the field has come:\n" + "\n".join(
            f"- {caps.get(p['capability'], p['capability'])}: {p['status']}. {p['headline']}" for p in c["progress"]))
    if c.get("problems"):
        lines.append("Attempted but not solved well:\n" + "\n".join(
            f"- {p['id']}: {p['title']} (root cause: {p['diagnosis']['cause']})" for p in c["problems"]))
    if fw and c.get("ideas"):
        def label(d: dict[str, Any], v: str) -> str:
            return next((x["label"] for x in d["values"] if x["id"] == v), "?")
        lines.append("The researcher's ideas on the map:\n" + "\n".join(
            f"- {cid}: " + "; ".join(f"{d['name']}={label(d, i['values'][d['id']])}" for d in fw["dims"] if d["id"] in i["values"])
            + (f"; answers {i['problem']}" if i.get("problem") else "") + (f". {i['note']}" if i.get("note") else "")
            for cid, i in c["ideas"].items()))
    text = "\n".join(lines)
    return text if len(text) <= max_chars else text[: max_chars - 1] + "…"


def resume_from(content: dict[str, Any]) -> str:
    done = set(content.get("done") or [])
    return next((s for s in STEPS if s not in done), "read_next")


# ---------------------------------------------------------------------------
# The loops back up the chain


async def accept_proposal(b: Build, proposal_id: str) -> None:
    """A paper did not fit: revise the framework as the proposal says, re-place everything, re-check what follows."""
    prop = next((p for p in b.content.get("proposals") or [] if p["id"] == proposal_id), None)
    fw = chosen(b.content)
    if prop is None or fw is None:
        raise ValueError("No such proposal")
    paper = key_of(prop["paper_id"]) if prop.get("paper_id") else ""
    revised = await propose_framework(b, fw["by"], int(fw["id"][1:]) if fw["id"][1:].isdigit() else 1,
                                      revision=f"[{paper}] does not fit: {prop['text']}", current=fw)
    if not revised["dims"]:
        raise llm.ModelError("The revision gave no usable dimensions")
    revised["id"], revised["name"] = fw["id"], revised["name"] or fw["name"]
    revised["placements"] = await place(b, revised, _papers(b.map_id))
    held = _papers(b.map_id, ("held_out",))
    revised["held"] = await place(b, revised, held) if held else {}
    joined = _papers(b.map_id, ("joined",))
    revised["joined"] = {pid: p for pid, p in revised["placements"].items() if pid in {r["paper_id"] for r in joined}}
    revised["scores"] = score(revised)
    revised["scores"].update(await judge(b, revised))
    b.content["frameworks"] = [revised if f["id"] == fw["id"] else f for f in b.content["frameworks"]]
    for p in b.content.get("proposals") or []:
        if p["id"] == proposal_id:
            p["state"] = "accepted"
    _proposals_from_misfits(b.content)
    await run(b, start="trends", reason=f"Framework revised because [{paper}] did not fit: {prop['text'][:120]}",
              tell_steps=False)


async def choose_framework(b: Build, fw_id: str) -> None:
    if not any(f["id"] == fw_id for f in b.content.get("frameworks") or []):
        raise ValueError("No such framework")
    b.content["chosen"] = fw_id
    _proposals_from_misfits(b.content)
    name = chosen(b.content)["name"]
    await run(b, start="trends", reason=f"Chose the framework “{name}”", tell_steps=False)


async def replace_paper(b: Build, paper_id: str, note: str) -> None:
    """The researcher says a placement is wrong: place it again with their note; still strained, a proposal."""
    fw = chosen(b.content)
    row = next((r for r in _papers(b.map_id, ("core", "held_out", "joined")) if r["paper_id"] == paper_id), None)
    if fw is None or row is None:
        raise ValueError("No such paper in the map")
    got = (await place(b, fw, [row], note={paper_id: note})).get(paper_id)
    if got is None:
        raise llm.ModelError("The paper could not be placed again")
    got["corrected"] = note
    target = "held" if paper_id in fw["held"] else "joined" if paper_id in fw.get("joined", {}) else "placements"
    fw.setdefault(target, {})[paper_id] = got
    fw["scores"] = {**fw["scores"], **score(fw)}
    if got["strain"]:
        b.content.setdefault("proposals", []).append({"id": f"p{len(b.content['proposals']) + 1}_{paper_id[-4:]}",
                                                      "kind": "framework", "framework": fw["id"], "paper_id": paper_id,
                                                      "text": f"{got['note']} (the researcher: {note})", "state": "pending",
                                                      "at": time.time()})
    review_store.commit(b.map_id, b.content, f"[{key_of(paper_id)}] placed again: {note[:100]}")
    b.tell("version")


async def challenge(b: Build, text: str) -> dict[str, Any]:
    """Someone disputes the definition: what supports them, and what each way of redrawing the boundary would change."""
    d = b.content.get("definition") or {}
    w = d.get("working") or {}
    bounds = "\n".join(f"- {x.get('question')}: {x.get('decision')} ({x.get('why')})" for x in w.get("boundaries") or [])
    body = f"""Research direction: {b.topic}
Working definition: {w.get('text', '')}
Its boundaries:
{bounds}
Capabilities it must cover:
{_capabilities(b.content)}

The researcher challenges it: "{text}"

1. "support": what in the literature supports the challenge, and what speaks against it (label each paragraph).
2. "options": two or three ways to redraw the boundary in answer (keeping it as it is may be one). For each: the new \
boundary decision in one sentence; what it changes: which dimension would be added or changed, which kinds of papers \
would join or leave the map, which capabilities' progress would change; and a search query that finds papers it \
would bring in.

JSON: {{"support": {PARAGRAPHS}, "options": [{{"boundary": "...", "dimensions": "...", "papers": "...", "progress": "...", "search": "..."}}]}}"""
    out = await llm.ask(b.access, "challenge", body)
    options = []
    for i, o in enumerate(out.get("options") or []):
        if not isinstance(o, dict) or not o.get("boundary"):
            continue
        joins: list[str] = []
        if o.get("search"):
            try:
                hits = await b.lit.search(str(o["search"]), by="Field map", context="A challenge to the definition", limit=6)
                joins = [e["paper_id"] for e in hits]
            except Exception:
                joins = []
        options.append({"id": f"o{i + 1}", "boundary": str(o["boundary"]), "dimensions": str(o.get("dimensions", "")),
                        "papers": str(o.get("papers", "")), "progress": str(o.get("progress", "")), "joins": joins})
    ch = {"id": f"x{len(b.content.get('challenges') or []) + 1}", "text": text, "support": paragraphs(out.get("support") or []),
          "options": options, "state": "pending", "at": time.time()}
    b.content.setdefault("challenges", []).append(ch)
    b.save()
    b.tell(f"challenge:{ch['id']}")
    return ch


async def apply_challenge(b: Build, challenge_id: str, option_id: str) -> None:
    """Redraw the boundary: papers it brings in join the map (with cards), and the map is rebuilt from the definition."""
    ch = next((c for c in b.content.get("challenges") or [] if c["id"] == challenge_id), None)
    opt = next((o for o in (ch or {}).get("options") or [] if o["id"] == option_id), None)
    if ch is None or opt is None:
        raise ValueError("No such option")
    b.content.setdefault("decided", []).append(opt["boundary"])
    have = {r["paper_id"] for r in review_store.papers(b.map_id)}
    for pid in opt["joins"]:
        if pid not in have:
            review_store.add_paper(b.map_id, pid, "core")
    ch["state"], ch["chosen"] = "applied", option_id
    await step_cards(b)
    await run(b, start="definition", reason=f"Definition redrawn after a challenge: {opt['boundary'][:140]}",
              tell_steps=False)


async def check_new_papers(b: Build) -> list[str]:
    """Papers that joined the space's literature since the map was built: the relevant ones are placed in it."""
    m = review_store.map(b.map_id)
    fw = chosen(b.content)
    if fw is None or m is None:
        return []
    have = {r["paper_id"] for r in review_store.papers(b.map_id)}
    owner = "" if b.campaign_id else b.owner
    new = [i for i in library.items(b.campaign_id or "", owner=owner)
           if i["paper_id"] not in have and i["added_at"] > (m["checked_at"] or m["created_at"])]
    review_store.set_state(b.map_id, checked_at=time.time())
    if not new:
        return []
    cands = [e for i in new if (e := _entry(i["paper_id"]))]
    lines = "\n".join(_brief_line({"key": e["key"], "paper": e["paper"]}, 60) for e in cands)
    out = await llm.ask(b.access, "relevant", f"""Research direction: {b.topic}
Capabilities:
{_capabilities(b.content)}
Papers that just joined the literature:
{lines}

Which belong in a map of this direction?
JSON: {{"relevant": ["Key"]}}""", temperature=0)
    keys = {e["key"]: e for e in cands}
    joined = [keys[k]["paper_id"] for k in (str(x).strip("[] ") for x in out.get("relevant") or []) if k in keys]
    if not joined:
        return []
    for pid in joined:
        review_store.add_paper(b.map_id, pid, "joined")
    await step_cards(b)
    rows = [r for r in _papers(b.map_id, ("joined",)) if r["paper_id"] in joined]
    placed = await place(b, fw, rows)
    fw.setdefault("joined", {}).update(placed)
    fw["scores"] = {**fw["scores"], **score(fw)}
    _proposals_from_misfits(b.content)
    strained = [key_of(pid) for pid, p in placed.items() if p["strain"]]
    review_store.commit(b.map_id, b.content, f"{len(joined)} new paper(s) placed" +
                        (f"; {', '.join(strained)} did not fit and proposes a revision" if strained else ""))
    b.tell("version")
    return joined
