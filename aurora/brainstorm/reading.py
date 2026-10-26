"""The agents reading a paper with the researcher: marking passages, and talking about one.

* **Reading along**: each agent reads the paper for the idea under discussion and marks the passages
  its role cares about (the Librarian: what the paper shows for or against the idea; the Skeptic: what
  the idea must survive; Methods: what a test of it could reuse). A mark quotes the paper word for word,
  so it can be found on the page again; a quote the paper does not contain is dropped.
* **A conversation on a passage**: the researcher asks about a passage (or replies in its thread) and
  the agent answers from the passage, its page and the idea.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from aurora.brainstorm import agents as ag
from aurora.literature.annotations import RELATIONS, find_page, fold_text

logger = logging.getLogger(__name__)

MAX_MARKS = 2
MAX_PAPER_CHARS = 60_000

READ_FOR = {
    "Librarian": "passages that are evidence for or against the idea: what the paper shows, measured how, on what",
    "Skeptic": "passages the idea has to survive: a limit of scope, a confounder, a measurement problem, a result "
               "that cuts against it",
    "Methods": "passages a test of the idea could reuse: a dataset, a protocol, a metric, a baseline, a setup and "
               "what it costs",
}

_READ = """\
You are the {name} in AURORA, reading a paper together with a researcher. You {role}.

The researcher's idea{plural}:
{ideas}

Read the paper below and mark up to {n} {read_for}. Mark only what really bears on the idea; mark nothing \
if nothing does.
{already}
For each mark give:
- "quote": the exact words of the paper, copied character for character from the text below (one sentence \
or a clause of one, 10 to 40 words). Do not paraphrase, shorten inside or fix the quote.
- "comment": one to three sentences, plain prose, on what the passage means for the idea. Concrete; no \
praise; say what to do about it when you can.
- "rel": "supports", "challenges", "asks about" or "builds on" (how the passage bears on the idea).
- "idea": the ID of the idea it bears on.

Answer with JSON only: {{"marks": [{{"quote": "...", "comment": "...", "rel": "...", "idea": "..."}}]}}

The paper [{key}] {title}:

{text}"""

_TALK = """\
You are the {name} in AURORA: you {role}. You are reading the paper [{key}] {title} with a researcher.

{idea}The passage on page {page}:
«{passage}»

Around it on the page:
{around}

Answer the researcher's latest message in one to four sentences of plain prose: what the passage means \
for the idea, from your role. Use only what the paper says here; if the answer needs another part of the \
paper, say where to look. Cite other papers only by their key in square brackets and only papers you \
know are in the project's literature; never invent one. No headings, no lists."""


def _json(text: str) -> dict[str, Any]:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}


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


async def read_along(agent: str, *, key: str, title: str, pages: list[str], ideas: list[dict[str, Any]],
                     access: ag.ModelAccess, already: list[str] | None = None, client: Any = None) -> list[dict[str, Any]]:
    """The passages an agent marks: ``[{quote, comment, rel, card_id, page_index}]`` (quotes the paper has)."""
    if not access.api_key or not pages or not ideas:
        return []
    ids = {i["card_id"] for i in ideas}
    prompt = _READ.format(
        name=agent, role=ag.AGENTS[agent]["role"], plural="s" if len(ideas) > 1 else "",
        ideas="\n".join(f"- {i['card_id']}: {i['text']}" for i in ideas), n=MAX_MARKS, read_for=READ_FOR[agent],
        already=("Already marked by others (do not mark these again):\n" + "\n".join(f"- «{q[:200]}»" for q in already)
                 + "\n") if already else "",
        key=key, title=title, text=_paper_text(pages))
    client = client or ag._client(access)
    resp = await client.chat.completions.create(model=access.model, temperature=0.3, max_tokens=3000,
                                                messages=[{"role": "user", "content": prompt}])
    marks = _json(resp.choices[0].message.content or "").get("marks") or []
    taken = [fold_text(q) for q in already or []]
    out: list[dict[str, Any]] = []
    for m in marks[:MAX_MARKS]:
        if not isinstance(m, dict):
            continue
        quote = " ".join(str(m.get("quote", "")).split())
        page = find_page(pages, quote)
        folded = fold_text(quote)
        if page < 0 or any(folded in t or t in folded for t in taken if t):
            continue
        taken.append(folded)
        rel = str(m.get("rel", "")).lower()
        out.append({"quote": quote, "comment": " ".join(str(m.get("comment", "")).split())[:1500],
                    "rel": rel if rel in RELATIONS else "", "page_index": page,
                    "card_id": str(m.get("idea")) if m.get("idea") in ids else ideas[0]["card_id"]})
    return out


def _around(page_text: str, passage: str, width: int = 2500) -> str:
    """The page's text around the passage (the whole page when it is short)."""
    text = " ".join(page_text.split())
    if len(text) <= width:
        return text
    words = passage.split()
    probe = " ".join(words[:5]) if words else ""
    at = text.find(probe) if probe else -1
    start = max(0, (at if at >= 0 else 0) - width // 2)
    return ("…" if start else "") + text[start:start + width] + "…"


async def talk(agent: str, *, key: str, title: str, passage: str, page_index: int, page_text: str,
               idea: str, thread: list[tuple[str, str]], access: ag.ModelAccess, client: Any = None) -> str:
    """An agent's answer in the conversation on a passage. ``thread``: (speaker, text), the latest last."""
    if not access.api_key:
        return f"No API key is set: {access.missing}"
    system = _TALK.format(name=agent, role=ag.AGENTS[agent]["role"], key=key, title=title, page=page_index + 1,
                          passage=passage, around=_around(page_text, passage) or "(the page has no text layer)",
                          idea=f"The researcher's idea: {idea}\n\n" if idea else "")
    messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for who, text in thread[-20:]:
        messages.append({"role": "assistant" if who == agent else "user",
                         "content": text if who == agent else f"{who}: {text}"})
    client = client or ag._client(access)
    try:
        resp = await client.chat.completions.create(model=access.model, temperature=0.4, max_tokens=1500,
                                                    messages=messages)
        return (resp.choices[0].message.content or "").strip() or "(no answer)"
    except Exception as exc:
        logger.warning("%s on a passage failed", agent, exc_info=True)
        return f"I could not answer: {str(exc)[:300]}"
