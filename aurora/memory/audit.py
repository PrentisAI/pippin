"""A manuscript's references, before you submit it: whether each one exists, whether it was retracted, whether it
says what the sentence citing it says, and which key work is missing.

1. **Exists**: every .bib entry is looked up in the scholarly indexes (as any reference Pippin checks).
2. **Retracted**: the DOIs found are checked against OpenAlex and Crossref's retraction notices. They are also
   watched from then on: a later retraction interrupts you (``alerts``).
3. **Misquoted**: each sentence that cites a paper is read against the paper (its abstract, and what your notebook
   holds from it): does it support the sentence, partly, or not; numbers that differ are named. Only what does not
   fully hold is reported, with the paper's own words.
4. **Missing**: the manuscript's question is searched in the indexes and in your notebook and Library; a model names
   the work closest to its contribution that is not cited, and why it should be.

The report is advice: nothing in the manuscript is changed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from typing import Any

from aurora.memory.store import mem_store

logger = logging.getLogger(__name__)

CITE = re.compile(r"\\(?:cite|citep|citet|parencite|textcite|autocite|citealp|citeauthor|footcite)\*?"
                  r"(?:\[[^\]]*\]){0,2}\{([^}]*)\}")
MAX_PAIRS = 60
_tasks: set[asyncio.Task] = set()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS mem_audits (
    audit_id   TEXT PRIMARY KEY,
    owner      TEXT NOT NULL,
    title      TEXT NOT NULL DEFAULT '',
    state      TEXT NOT NULL DEFAULT 'running',   -- running | done | failed
    step       TEXT NOT NULL DEFAULT '',
    report     TEXT NOT NULL DEFAULT '{}',
    error      TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);
"""


def _db() -> Any:
    conn = mem_store._conn
    conn.executescript(_SCHEMA)
    return conn


def _save(audit_id: str, **fields: Any) -> None:
    cols = {k: (json.dumps(v) if k == "report" else v) for k, v in fields.items()}
    with mem_store._lock:
        _db().execute(f"UPDATE mem_audits SET {', '.join(f'{k}=?' for k in cols)} WHERE audit_id=?", (*cols.values(), audit_id))
        mem_store._conn.commit()


def get(audit_id: str) -> dict[str, Any] | None:
    with mem_store._lock:
        r = _db().execute("SELECT * FROM mem_audits WHERE audit_id=?", (audit_id,)).fetchone()
    if r is None:
        return None
    return {**dict(r), "report": json.loads(r["report"])}


def listing(owner: str) -> list[dict[str, Any]]:
    with mem_store._lock:
        rows = _db().execute("SELECT audit_id, title, state, step, created_at FROM mem_audits WHERE owner=? "
                             "ORDER BY created_at DESC LIMIT 30", (owner,)).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Reading the manuscript


def _plain(tex: str) -> str:
    """LaTeX as readable text (enough to judge a sentence): commands dropped, their text kept."""
    t = re.sub(r"(?<!\\)%.*", "", tex)
    t = CITE.sub(lambda m: f"[{m.group(1)}]", t)
    t = re.sub(r"\\(?:ref|eqref|label|autoref|cref)\{[^}]*\}", "", t)
    t = re.sub(r"\\[a-zA-Z]+\*?(?:\[[^\]]*\])?\{([^{}]*)\}", r"\1", t)
    t = re.sub(r"\\[a-zA-Z]+\*?", "", t)
    return re.sub(r"[{}]", "", t)


def citations(tex: str) -> list[dict[str, Any]]:
    """Each place a paper is cited: its keys and the sentence that cites it."""
    body = re.sub(r"(?<!\\)%.*", "", tex)
    out = []
    for m in CITE.finditer(body):
        start = max(body.rfind(". ", 0, m.start()) + 1, body.rfind("\n\n", 0, m.start()), m.start() - 500, 0)
        ends = [x for x in (body.find(". ", m.end()), body.find("\n\n", m.end())) if x >= 0]
        end = min(ends) if ends else len(body)
        sentence = " ".join(_plain(body[start:end + 1]).split()).strip(". ") + "."
        keys = [k.strip() for k in m.group(1).split(",") if k.strip()]
        out.append({"keys": keys, "sentence": sentence[:800], "line": body.count("\n", 0, m.start()) + 1})
    return out


def abstract_of(tex: str) -> str:
    m = re.search(r"\\begin\{abstract\}(.*?)\\end\{abstract\}", tex, re.S)
    if m:
        return " ".join(_plain(m.group(1)).split())[:3000]
    m = re.search(r"\\section\*?\{[^}]*\}(.*?)(\\section|\Z)", tex, re.S)
    return " ".join(_plain(m.group(1) if m else tex).split())[:3000]


def title_of(tex: str) -> str:
    m = re.search(r"\\title(?:\[[^\]]*\])?\{([^}]*)\}", tex)
    return " ".join(_plain(m.group(1)).split())[:200] if m else ""


# ---------------------------------------------------------------------------
# The audit


# ---------------------------------------------------------------------------
# A manuscript as a PDF: its reference list and where each reference is cited

_REFS_HEAD = re.compile(r"(?im)^\s*(?:\d+\.?\s*)?(references|bibliography|works cited|literature cited|参考文献)\s*$")
_NUM_CITE = re.compile(r"\[\s*(\d+(?:\s*[,–-]\s*\d+)*)\s*\]")
_AY_CITE = re.compile(r"\(([^()]{3,300}?(?:19|20)\d{2}[a-z]?)\)")
_AY_NARR = re.compile(r"\b([A-Z][A-Za-z'\-]+)(?:\s+et\s+al\.?|\s+(?:and|&)\s+[A-Z][A-Za-z'\-]+)?\s*\(((?:19|20)\d{2})[a-z]?\)")


def _numbers(spec: str) -> list[int]:
    out: list[int] = []
    for part in re.split(r"\s*,\s*", spec):
        m = re.fullmatch(r"(\d+)\s*[–-]\s*(\d+)", part)
        if m and int(m.group(2)) - int(m.group(1)) < 30:
            out += list(range(int(m.group(1)), int(m.group(2)) + 1))
        elif part.isdigit():
            out.append(int(part))
    return out


async def _split_references(access: Any, refs: str) -> list[str]:
    """A reference list without numbers, as one entry each (by a model: author-year lists have no reliable marks)."""
    from aurora.review import llm
    out: list[str] = []
    for i in range(0, min(len(refs), 60_000), 20_000):
        res = await llm.ask(access, "memory_split_references", f"""This is (part of) the reference list of a paper, \
as extracted from its PDF. Split it into its entries, each copied exactly as one line (join the lines it was broken \
over). Skip a partial entry at the very start or end.

{refs[i:i + 20_000]}

JSON: {{"entries": ["..."]}}""", want="entries", max_tokens=12000)
        out += [" ".join(str(e).split()) for e in res.get("entries") or [] if len(str(e)) > 20]
    return out


async def from_pdf(access: Any, data: bytes) -> tuple[str, list[tuple[str, Any]], str]:
    """A manuscript PDF as (its text with each citation written as \\cite{key}, its references, its title)."""
    from aurora.literature.agent_service import parse_citation
    from aurora.literature.pdfs import _text
    text, meta = _text(data, pages=120)
    heads = list(_REFS_HEAD.finditer(text))
    if not heads:
        raise ValueError("No reference list found in the PDF (a heading “References” or “Bibliography”)")
    body, refs = text[:heads[-1].start()], text[heads[-1].end():]
    refs = re.split(r"(?im)^\s*(?:appendix|supplementary material)\b", refs)[0]
    entries: list[tuple[str, Any]] = []
    numbered = re.findall(r"(?ms)^\s*\[(\d+)\]\s+(.+?)(?=^\s*\[\d+\]\s|\Z)", refs)
    if len(numbered) < 3:
        numbered = re.findall(r"(?ms)^\s*(\d{1,3})\.\s+(.+?)(?=^\s*\d{1,3}\.\s|\Z)", refs)
    if len(numbered) >= 3:
        entries = [(f"ref{n}", parse_citation(" ".join(t.split()))) for n, t in numbered]
        cited = _NUM_CITE.sub(lambda m: "\\cite{" + ",".join(f"ref{n}" for n in _numbers(m.group(1))) + "}", body)
    else:
        keys: dict[tuple[str, str], str] = {}
        for t in await _split_references(access, refs):
            ref = parse_citation(t)
            m = re.match(r"\s*([A-Z][A-Za-z'\-]+)", t) or re.match(r"\s*(\w+)", t)
            surname = m.group(1).lower() if m else "ref"
            year = str(ref.year or "")
            key = f"{surname}{year}"
            while any(k == key for k, _ in entries):
                key += "b"
            entries.append((key, ref))
            keys.setdefault((surname, year), key)

        def ay(names: str) -> str:
            out = []
            for piece in names.split(";"):
                m = re.search(r"([A-Z][A-Za-z'\-]+).*?((?:19|20)\d{2})", piece)
                if m and (k := keys.get((m.group(1).lower(), m.group(2)))):
                    out.append(k)
            return ("\\cite{" + ",".join(out) + "}") if out else ""
        cited = _AY_CITE.sub(lambda m: ay(m.group(1)) or m.group(0), body)
        cited = _AY_NARR.sub(lambda m: (m.group(0) + " " + ay(f"{m.group(1)} {m.group(2)}")) if ay(f"{m.group(1)} {m.group(2)}")
                             else m.group(0), cited)
    cited = cited.replace("%", "\\%")
    title = str((meta or {}).get("title") or "").strip()
    if len(title.split()) < 3:              # often empty: the longest plausible line before the abstract
        head = re.split(r"(?i)\babstract\b", body, maxsplit=1)[0]
        lines = [ln.strip() for ln in head.splitlines()[:25] if 2 <= len(ln.split()) <= 25 and "@" not in ln
                 and not re.search(r"(?i)permission|copyright|licen[cs]e|arxiv|preprint|conference|university|institute", ln)]
        def caps(ln: str) -> float:          # titles are mostly Title Case
            words = [w for w in re.findall(r"[A-Za-z][\w'-]*", ln) if w.lower() not in ("a", "an", "the", "of", "for", "and",
                                                                                     "in", "on", "to", "with", "via", "is")]
            return sum(w[0].isupper() for w in words) / max(1, len(words))
        titled = [ln for ln in lines if caps(ln) >= 0.6]
        title = (titled[0] if titled else max(lines, key=len) if lines else "")[:200]
    m = re.search(r"(?is)\babstract\b[.:—-]?\s*(.+?)(?:\n\s*(?:1\.?|I\.?)\s+introduction|\n\s*introduction\b)", body)
    abstract = " ".join((m.group(1) if m else body[:2500]).split())[:3000]
    tex = f"\\title{{{title}}}\n\\begin{{abstract}}{abstract}\\end{{abstract}}\n{cited}"
    return tex, entries, title


def start(access: Any, models: dict[str, str], owner: str, tex: str, bib: str, title: str = "", *,
          pdf: bytes = b"") -> dict[str, Any]:
    aid = f"au_{uuid.uuid4().hex[:12]}"
    with mem_store._lock:
        _db().execute("INSERT INTO mem_audits (audit_id, owner, title, created_at) VALUES (?,?,?,?)",
                      (aid, owner, (title or title_of(tex) or "Manuscript")[:200], time.time()))
        mem_store._conn.commit()
    task = asyncio.create_task(_run(access, models, owner, aid, tex, bib, pdf))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return get(aid)  # type: ignore[return-value]


async def _run(access: Any, models: dict[str, str], owner: str, aid: str, tex: str, bib: str, pdf: bytes = b"") -> None:
    try:
        entries = None
        if pdf:
            _save(aid, step="Reading the PDF: its reference list and its citations")
            tex, entries, title = await from_pdf(access.with_model(models["memory"]), pdf)
            if title:
                _save(aid, title=title)
        report = await run(access, models, owner, tex, bib, step=lambda s: _save(aid, step=s), entries=entries)
        # a new paper doing what this manuscript claims will interrupt you from now on
        from aurora.memory import alerts
        alerts.remember_manuscript(owner, aid, report.get("title") or title_of(tex), abstract_of(tex))
        _save(aid, state="done", step="", report=report)
    except Exception as exc:
        logger.info("audit %s failed", aid, exc_info=True)
        _save(aid, state="failed", error=str(exc)[:500])


async def run(access: Any, models: dict[str, str], owner: str, tex: str, bib: str, *, step: Any = None,
              entries: list[tuple[str, Any]] | None = None) -> dict[str, Any]:
    from aurora.auth.store import auth_store
    from aurora.literature.bibtex import parse
    from aurora.literature.check import check
    from aurora.literature.library import library
    from aurora.memory import alerts
    say = step or (lambda s: None)
    entries = entries if entries is not None else parse(bib)
    cited = {k for c in citations(tex) for k in c["keys"]}
    say(f"Looking up {len(entries)} references")
    sem = asyncio.Semaphore(6)

    async def look(key: str, ref: Any) -> dict[str, Any]:
        async with sem:
            try:
                r = await check(ref)
            except Exception as exc:
                return {"key": key, "title": ref.title, "status": "unknown", "reason": str(exc)[:200]}
        row = {"key": key, "title": ref.title, "status": r.status, "reason": r.reason, "cited": key in cited}
        if r.verified and r.paper is not None:
            stored = library.upsert_paper(r.paper)
            row.update(paper_id=stored["paper_id"], doi=r.paper.doi or "", found_title=r.paper.title,
                       abstract=(r.paper.abstract or "")[:2500], year=r.paper.year)
        return row
    rows = list(await asyncio.gather(*(look(k, ref) for k, ref in entries)))
    say("Checking for retractions")
    dois = [r["doi"].lower() for r in rows if r.get("doi")]
    retracted = await alerts.is_retracted(dois) if dois else {}
    for r in rows:
        if r.get("doi", "").lower() in retracted:
            r["retracted"] = retracted[r["doi"].lower()]
        if r.get("doi"):
            mem_store.set_retraction(r["doi"], bool(r.get("retracted")), r.get("retracted", ""))
    # what the manuscript cites is relied on: watched for retractions from now on
    watched = auth_store.get_pref(owner, "memory_audited_dois", {}) or {}
    for r in rows:
        if r.get("doi"):
            watched[r["doi"].lower()] = {"title": r.get("found_title") or r["title"], "where": "a manuscript you checked"}
    auth_store.set_pref(owner, "memory_audited_dois", watched)
    say("Reading each citation against the paper")
    miscites = await _miscites(access.with_model(models["memory"]), owner, tex, {r["key"]: r for r in rows})
    say("Looking for key work that is not cited")
    missing = await _missing(access.with_model(models["memory"]), owner, tex, rows, models=models)
    unused = sorted(k for k, _ in entries if k not in cited)
    undefined = sorted(cited - {k for k, _ in entries})
    return {"title": title_of(tex), "entries": rows, "miscites": miscites, "missing": missing,
            "unused": unused, "undefined": undefined,
            "counts": {"entries": len(rows), "verified": sum(r["status"] == "verified" for r in rows),
                       "flagged": sum(r["status"] not in ("verified",) for r in rows),
                       "retracted": sum(1 for r in rows if r.get("retracted")), "miscites": len(miscites),
                       "missing": len(missing)}}


def _memory_text(owner: str, paper_id: str) -> str:
    """What the person's memory holds from a paper (its claims and their words)."""
    srcs = [s for s in mem_store.sources(owner, 2000) if s["paper_id"] == paper_id and s["state"] == "done"]
    lines = []
    for s in srcs[:1]:
        for uid in s["result"].get("units") or []:
            u = mem_store.unit(uid)
            if u:
                quotes = [e["quote"] for e in mem_store.evidence(uid) if e["source_id"] == s["source_id"]]
                lines.append(f"- {u['claim']} (“{quotes[0][:300]}”)" if quotes else f"- {u['claim']}")
    return "\n".join(lines)


async def _miscites(access: Any, owner: str, tex: str, by_key: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    from aurora.review import llm
    pairs = []
    for c in citations(tex):
        for k in c["keys"]:
            r = by_key.get(k)
            if r and r.get("paper_id") and (r.get("abstract") or _memory_text(owner, r["paper_id"])):
                pairs.append((c, k, r))
    pairs = pairs[:MAX_PAIRS]
    out: list[dict[str, Any]] = []
    for i in range(0, len(pairs), 8):
        batch = pairs[i:i + 8]
        body = "\n\n".join(f"#{n + 1}\nSentence: {c['sentence']}\nCites [{k}]: {r.get('found_title') or r['title']}\n"
                           f"Abstract: {r.get('abstract') or '(none)'}\n"
                           f"From it, in the researcher's memory:\n{_memory_text(owner, r['paper_id']) or '(nothing)'}"
                           for n, (c, k, r) in enumerate(batch))
        try:
            res = await llm.ask(access, "memory_miscite", f"""For each sentence of a manuscript and a paper it cites: \
does the paper, as far as its abstract and the researcher's notes show, support what the sentence says it does?
- "verdict": supports | partly | no | unclear (unclear when the abstract cannot tell);
- "issue": for partly or no, what does not hold, concretely (a number that differs, a claim stronger than the \
paper's, a different setting); "" otherwise;
- "quote": the paper's own words (from the abstract or the notes, copied exactly) that show it, "" if none.

{body}

JSON: {{"items": [{{"n": 1, "verdict": "supports", "issue": "", "quote": ""}}]}}""", want="items", max_tokens=6000)
        except Exception:
            logger.info("judging citations failed", exc_info=True)
            continue
        for x in res.get("items") or []:
            try:
                c, k, r = batch[int(x.get("n")) - 1]
            except (TypeError, ValueError, IndexError):
                continue
            if x.get("verdict") in ("partly", "no"):
                quote = str(x.get("quote") or "")
                hay = (r.get("abstract") or "") + _memory_text(owner, r["paper_id"])
                out.append({"key": k, "sentence": c["sentence"], "line": c["line"], "verdict": x["verdict"],
                            "issue": llm.unlabel(x.get("issue", "")),
                            "quote": quote if quote and quote[:40] in hay else "", "title": r.get("found_title") or r["title"]})
    return out


async def _missing(access: Any, owner: str, tex: str, rows: list[dict[str, Any]], *,
                   models: dict[str, str] | None = None) -> list[dict[str, Any]]:
    from aurora.literature.indexes import indexes
    from aurora.literature.library import library
    from aurora.review import llm
    abstract = abstract_of(tex)
    if len(abstract) < 100:
        return []
    have = {r.get("paper_id") for r in rows} | {(r.get("found_title") or r["title"]).lower() for r in rows}
    try:
        q = await llm.ask(access, "memory_missing_queries", f"""A manuscript's abstract:
{abstract}

Write four short search queries (a few words each) that would find the work closest to its contribution: the \
same question, the same method, the strongest competing approach, the benchmark it should compare with.

JSON: {{"queries": ["..."]}}""", want="queries", max_tokens=3000)
        queries = [str(x) for x in q.get("queries") or []][:4]
    except Exception:
        queries = []
    cands: dict[str, Any] = {}
    idx = indexes()
    for query in queries:
        try:
            found = await idx.search(query, 8)
        except Exception:
            continue
        for p in found.papers:
            if p.title and p.authors and p.title.lower() not in have:
                e = library.upsert_paper(p)
                if e["paper_id"] not in have:
                    cands.setdefault(e["paper_id"], e)
    for s in mem_store.sources(owner, 500):            # what the researcher saved counts too
        if s["paper_id"] and s["paper_id"] not in have and (e := library.paper(s["paper_id"])):
            cands.setdefault(e["paper_id"], e)
    # the map of the field the manuscript sits in: its papers, and where the field stands
    field = ""
    if models:
        try:
            from aurora.memory.surface import map_for
            from aurora.review.fieldmap import summary_for_agents
            from aurora.review.store import review_store
            fmap = await map_for(access, models, owner, abstract)
        except Exception:
            fmap = None
        if fmap:
            field = f"\nThe map of the field it sits in (“{fmap['topic']}”):\n{summary_for_agents(fmap, 3000)}\n"
            for r in review_store.papers(fmap["map_id"]):
                if r["role"] in ("core", "joined", "benchmark") and r["paper_id"] not in have and (e := library.paper(r["paper_id"])):
                    cands.setdefault(e["paper_id"], e)
    if not cands:
        return []
    listing_ = "\n".join(f"[{pid}] {e['paper'].title} ({e['paper'].year or '?'}) — {(e['paper'].abstract or '')[:300]}"
                         for pid, e in list(cands.items())[:60])
    cited_titles = "\n".join(f"- {r.get('found_title') or r['title']}" for r in rows[:80])
    res = await llm.ask(access, "memory_missing", f"""A manuscript's abstract:
{abstract}

It cites:
{cited_titles}
{field}

Candidates it does not cite:
{listing_}

Which candidates would a reviewer expect to see cited, because they are closest to the contribution (the same \
question, a direct competitor, the standard baseline or benchmark)? At most six, the most important first, each \
with one sentence why. Be strict: leave out work that is only on the same topic.

JSON: {{"missing": [{{"id": "...", "why": "..."}}]}}""", want="missing", max_tokens=5000)
    out = []
    for x in res.get("missing") or []:
        e = cands.get(str(x.get("id") or ""))
        if e:
            from aurora.literature.bibtex import entry
            out.append({"paper_id": e["paper_id"], "title": e["paper"].title, "byline": e["paper"].byline(),
                        "why": llm.unlabel(x.get("why", "")), "bibtex": entry(e["key"], e["paper"])})
    return out[:6]
