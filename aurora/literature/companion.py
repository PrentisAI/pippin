"""Pippin reading a paper with the researcher: notes in the margin that are worth thinking about, and, where a
passage is easier to understand by trying it than by reading it, a figure to play with.

Pippin reads the paper against what the researcher is doing (the project's objective, what its runs set out to
test, the brainstorm's idea, their open questions, what they clipped from other papers) and writes a few notes,
each on a passage it quotes word for word:

* ``orient``: at the start of a section that matters, what to read it for, given their work;
* ``question``: a question for them to think about, which the paper raises for their work;
* ``connect``: where the passage meets something of theirs (a run, a clip, a question);
* ``challenge``: an assumption of the paper, or of theirs, worth doubting here.

It decides by itself whether a passage deserves a figure: a quantity that changes with a setting, a trade-off,
numbers in a table that are easier to compare by moving something. Then it writes a small page (HTML with its
script, no network) drawn from the paper's own numbers, shown in a gap opened under the passage. Most papers get no
figure; words do most things well.

The notes are suggestions until the researcher keeps them, like the other agents' marks.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from aurora.brainstorm import agents as ag
from aurora.literature.annotations import find_page, fold_text

logger = logging.getLogger(__name__)

KINDS = ("orient", "question", "connect", "challenge")
REL = {"question": "asks about", "challenge": "challenges", "connect": "builds on", "orient": ""}
MAX_NOTES = 6
MAX_PAPER_CHARS = 60_000
MAX_FIGURE = 60_000

_READ = """\
You are Pippin, reading a paper alongside a researcher. You do not summarise the paper for them, and you do not \
think for them: you write a few notes in its margin that make them think harder about it, the way a good colleague \
would pencil remarks into a paper they hand over.

What the researcher is working on:
{context}

Write up to {n} notes on the paper below. Each note sits beside a passage. Kinds of note:
- "orient": at the start of a section that matters for their work, one or two sentences on what to read it for. \
Only for the sections that really matter; skip the rest.
- "question": a question the passage raises for their work, which they should answer for themselves. Ask it; do \
not answer it.
- "connect": where the passage meets something of theirs above (a run, a question, a clip). Name it.
- "challenge": an assumption here (the paper's, or theirs) worth doubting, and why.

Fewer, sharper notes are better than many. No praise, no summary, no generic advice. Ground every note in their \
work above when you can.

A figure: if, and only if, one passage would be much clearer by trying it than by reading it (a quantity that \
changes with a setting, a trade-off, numbers in a table easier to compare by moving a slider, a process over time), \
add "figure" to that note: a brief for an interactive figure (what to show, what the reader can change, and the \
paper's own numbers it uses). At most one figure, and none when words do the job.

For each note give:
- "quote": the exact words of the paper, copied character for character from the text below (a clause or a \
sentence, 8 to 40 words). For "orient", quote the first sentence of the section. Do not paraphrase or shorten.
- "kind": one of "orient", "question", "connect", "challenge".
- "note": one to three sentences, plain prose.
- "figure": (optional) the brief, as above.

Answer with JSON only: {{"notes": [{{"quote": "...", "kind": "...", "note": "...", "figure": "..."}}]}}

The paper [{key}] {title}:

{text}"""

_FIGURE = """\
Write a small interactive figure for a researcher reading a paper. It is shown in a gap in the page, under the \
passage it is about, inside a sandboxed frame about 560 pixels wide.

The passage (page {page} of [{key}] {title}):
«{quote}»

What to show:
{brief}

The paper's text around the passage, for its numbers:
{around}

Rules:
- One complete HTML document: inline <style> and <script>, nothing loaded from the network (no CDN, no fonts, no \
images from elsewhere). Draw with SVG or a canvas.
- Use the paper's own numbers where it gives them. Anything you have to assume (a curve between two reported \
points, a made-up example signal) is labelled as assumed, in the figure.
- One or two controls (a slider, a few buttons) that change what is drawn at once. Short labels, a title of a few \
words, one line under it saying what to try.
- Plain, quiet styling: system sans-serif font, 13px, dark grey text on white, one accent colour (#2f6fde), thin \
lines. At most 340 pixels tall. Round every number shown.
- No explanation outside the figure; no comments in the code.

Answer with the HTML document only."""


def model_client(access: ag.ModelAccess, timeout: float) -> Any:
    """The model's client, giving up after ``timeout`` seconds (a long answer must not hang a reading)."""
    c = ag._client(access)
    return c.with_options(timeout=timeout) if hasattr(c, "with_options") else c


def _json(text: str) -> dict[str, Any]:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}


def _notes_of(text: str) -> list[Any]:
    """The notes in the model's answer; when the answer was cut off, the notes that came through whole."""
    got = _json(text).get("notes")
    if isinstance(got, list):
        return got
    out: list[Any] = []
    dec = json.JSONDecoder()
    for m in re.finditer(r'\{\s*"quote"', text or ""):
        try:
            obj, _ = dec.raw_decode(text, m.start())
        except json.JSONDecodeError:
            continue
        out.append(obj)
    return out


def _paper_text(pages: list[str]) -> str:
    out, total = [], 0
    for i, p in enumerate(pages):
        chunk = f"--- page {i + 1} ---\n{p.strip()}\n"
        if total + len(chunk) > MAX_PAPER_CHARS:
            out.append(f"--- (the paper goes on for {len(pages) - i} more pages) ---")
            break
        out.append(chunk)
        total += len(chunk)
    return "\n".join(out)


def _around(page_text: str, quote: str, width: int = 3000) -> str:
    text = " ".join(page_text.split())
    if len(text) <= width:
        return text
    words = quote.split()
    at = text.find(" ".join(words[:5])) if words else -1
    start = max(0, (at if at >= 0 else 0) - width // 2)
    return ("…" if start else "") + text[start:start + width] + "…"


def context_of(owner: str, campaign_id: str, idea: str = "") -> str:
    """What the researcher is working on, in a few lines: the project, what its runs test, the brainstorm's idea,
    their open questions, and what they clipped from other papers."""
    lines: list[str] = []
    if campaign_id:
        try:
            from aurora.api.routes.campaigns import _ensure_campaign
            from aurora.notebook import entry as nb_entry
            c = _ensure_campaign(campaign_id)
            lines.append(f"The project: {c.name}. Its objective: {(c.objective or '').strip()[:800]}")
            runs = sorted(c.experiments.values(), key=lambda e: e.started_at or 0)[-3:]
            for e in runs:
                try:
                    p = nb_entry.entry(e)
                except Exception:
                    continue
                said = p.get("hypothesis") or p.get("idea", {}).get("description") or ""
                if said:
                    lines.append(f"- A run, {e.task_name or e.run_id} ({e.status}), tests: {str(said)[:400]}")
        except Exception:
            logger.debug("reading the project for Pippin's notes failed", exc_info=True)
    if idea:
        lines.append(f"The idea they are thinking through in a brainstorm: {idea[:800]}")
    try:
        from aurora.memory import questions
        qs = [q["text"] for q in questions.current(owner)][:5]
        if qs:
            lines.append("Their open questions:\n" + "\n".join(f"- {q[:300]}" for q in qs))
    except Exception:
        logger.debug("reading the questions for Pippin's notes failed", exc_info=True)
    try:
        from aurora.notebook.store import nb_store
        clips = nb_store.clips(owner)[:8]
        if clips:
            lines.append("What they clipped from other papers lately (their words after the arrow):\n" + "\n".join(
                f"- [{c['paper_title'][:80]}] «{c['quote'][:200]}»" + (f" → {c['words'][:200]}" if c["words"] else "")
                for c in clips))
    except Exception:
        logger.debug("reading the clips for Pippin's notes failed", exc_info=True)
    return "\n".join(lines) or "Nothing written down yet: read for what a researcher new to the topic should think about."


async def read(*, key: str, title: str, pages: list[str], context: str, access: ag.ModelAccess,
               already: list[str] | None = None, client: Any = None) -> list[dict[str, Any]]:
    """Pippin's notes on the paper: ``[{quote, kind, note, rel, page_index, figure_brief}]`` (quotes the paper has)."""
    if not access.api_key or not any(p.strip() for p in pages):
        return []
    client = client or model_client(access, 240)
    prompt = _READ.format(context=context, n=MAX_NOTES, key=key, title=title, text=_paper_text(pages))
    resp = await client.chat.completions.create(model=access.model, temperature=0.4, max_tokens=12000,
                                                messages=[{"role": "user", "content": prompt}])
    got = _notes_of(resp.choices[0].message.content or "")
    taken = [fold_text(q) for q in already or []]
    out: list[dict[str, Any]] = []
    figure = False
    for m in got[:MAX_NOTES]:
        if not isinstance(m, dict):
            continue
        quote, page = _anchor(pages, " ".join(str(m.get("quote", "")).split()))
        folded = fold_text(quote)
        if page < 0 or any(folded in t or t in folded for t in taken if t):
            continue
        taken.append(folded)
        kind = str(m.get("kind", "")).lower()
        kind = kind if kind in KINDS else "question"
        brief = " ".join(str(m.get("figure") or "").split())
        if figure:
            brief = ""
        figure = figure or bool(brief)
        out.append({"quote": quote, "kind": kind, "note": " ".join(str(m.get("note", "")).split())[:1500],
                    "rel": REL[kind], "page_index": page, "figure_brief": brief[:1500]})
    logger.info("Pippin read [%s]: %d notes written, %d found in the paper", key, len(got), len(out))
    return out


def _anchor(pages: list[str], quote: str) -> tuple[str, int]:
    """The quote as the paper has it, and its page: the whole quote, else the longest run of its words the paper
    has (a model sometimes mends a word or two); ('', -1) when not even eight words of it are there."""
    page = find_page(pages, quote)
    if page >= 0:
        return quote, page
    words = quote.split()
    for size in range(len(words) - 1, 7, -1):
        for start in range(0, len(words) - size + 1):
            part = " ".join(words[start:start + size])
            page = find_page(pages, part)
            if page >= 0:
                return part, page
    return "", -1


def clean_figure(html: str) -> str:
    """The figure's page as written, out of a code fence; '' when it is not a page."""
    text = (html or "").strip()
    m = re.search(r"```(?:html)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1).strip()
    if "<" not in text or not re.search(r"<(svg|canvas|body|html|div)", text, re.I):
        return ""
    return text[:MAX_FIGURE]


async def figure(*, key: str, title: str, quote: str, page_index: int, page_text: str, brief: str,
                 access: ag.ModelAccess, client: Any = None) -> str:
    """A small interactive figure for the passage, as a whole HTML page ('' when none could be made)."""
    if not access.api_key or not brief:
        return ""
    client = client or model_client(access, 300)
    prompt = _FIGURE.format(key=key, title=title, page=page_index + 1, quote=quote, brief=brief,
                            around=_around(page_text, quote) or "(the page has no text layer)")
    resp = await client.chat.completions.create(model=access.model, temperature=0.3, max_tokens=16000,
                                                messages=[{"role": "user", "content": prompt}])
    return clean_figure(resp.choices[0].message.content or "")
