"""The page open in the browser, for asking Pippin about it from the extension's side panel.

The extension sends the page as the person sees it (signed-in pages too) with their selection. A paper's page
(arXiv, a DOI) is read from the paper's PDF instead, so a question about an abstract page reaches the whole paper.
Kept in memory only, per person, until another page is asked about; nothing is saved until they keep something.
"""

from __future__ import annotations

import logging
import re
import time
from collections import Counter
from math import log
from typing import Any

logger = logging.getLogger(__name__)

PART = 4000              # a page's text without pages of its own is cut into parts of about this many characters
PASSAGE = 1200           # what one passage of an answer's evidence holds
WHOLE = 14000            # a text this short is given whole
_pages: dict[str, dict[str, Any]] = {}


def _parts(text: str) -> list[str]:
    """A page's text in parts (cut at paragraph breaks), standing in for a PDF's pages."""
    parts, cur = [], ""
    for para in re.split(r"\n\s*\n", text):
        if cur and len(cur) + len(para) > PART:
            parts.append(cur)
            cur = ""
        cur = f"{cur}\n\n{para}" if cur else para
    return [*parts, cur] if cur.strip() else parts


async def load(owner: str, *, url: str, title: str = "", text: str = "", selection: str = "") -> dict[str, Any]:
    """What is open, read once per address: a paper's PDF, else the text the browser sent."""
    url = url.split("#")[0].strip()
    have = _pages.get(owner)
    if have and have["url"] == url and (have["texts"] or not text):
        have.update(selection=" ".join(selection.split())[:4000], at=time.time(), title=title or have["title"])
        return have
    page: dict[str, Any] = {"url": url, "title": title.strip()[:300], "selection": " ".join(selection.split())[:4000],
                            "texts": [], "source": "page", "paper_id": "", "at": time.time(), "passage": ""}
    paper = None
    try:
        from aurora.memory.relate import _paper
        paper = await _paper(url) if url else None
    except Exception:
        logger.info("no paper for %s", url, exc_info=True)
    if paper:
        page.update(paper_id=paper["paper_id"], title=paper["title"] or page["title"])
        try:
            from aurora.api.routes.reader import _texts
            from aurora.literature.library import library
            texts = await _texts(library.paper(paper["paper_id"]), "")
            if texts and any(t.strip() for t in texts):
                page.update(texts=texts, source="pdf")
        except Exception:
            logger.info("the PDF of %s could not be read", paper["paper_id"], exc_info=True)
    if not page["texts"] and text.strip():
        page["texts"] = _parts(text.strip()[:200000])
    _pages[owner] = page
    return page


def current(owner: str) -> dict[str, Any] | None:
    return _pages.get(owner)


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]{3,}|[一-鿿]", text.lower())


def passages(owner: str, question: str, k: int = 6) -> dict[str, Any]:
    """The page's passages that bear on the question (the whole text when it is short), the selection first."""
    page = _pages.get(owner)
    if not page:
        return {"error": "No page was sent from the browser with this question."}
    where = "page" if page["source"] == "pdf" else "part"
    head = {"title": page["title"], "url": page["url"],
            "read_from": "the paper's PDF" if page["source"] == "pdf" else "the page as the browser shows it",
            "selected": page["selection"]}
    if not page["texts"]:
        return {**head, "error": "The page's text could not be read (a browser page, or an empty one)."}
    total = sum(len(t) for t in page["texts"])
    if total <= WHOLE:
        page["passage"] = page["selection"] or page["texts"][0][:PASSAGE]
        return {**head, "text": "\n\n".join(f"[{where} {i + 1}]\n{t}" for i, t in enumerate(page["texts"]))}
    chunks = [(i, t[s:s + PASSAGE]) for i, t in enumerate(page["texts"]) for s in range(0, len(t), PASSAGE)]
    docs = [Counter(_words(c)) for _, c in chunks]
    df = Counter(w for d in docs for w in d)
    q = Counter(_words(f"{question} {page['selection']}"))

    def score(j: int) -> float:
        d = docs[j]
        return sum(min(d[w], 3) * log(1 + len(docs) / df[w]) * q[w] for w in q if w in d)
    sel = page["selection"][:200].lower()
    order = sorted(range(len(chunks)), key=lambda j: (-(bool(sel) and sel[:60] in chunks[j][1].lower()), -score(j), j))
    top = sorted(order[:k])
    page["passage"] = page["selection"] or chunks[order[0]][1]
    return {**head, "passages": [{where: chunks[j][0] + 1, "text": chunks[j][1]} for j in top],
            "note": f"{len(top)} of {len(chunks)} passages, the ones that share words with the question; "
                    "ask again with other words for other parts."}


def anchor(owner: str) -> tuple[str, int]:
    """A quote to hang a kept answer on, and its page: the selection, else the opening of the passage it drew on."""
    page = _pages.get(owner) or {}
    from aurora.literature.companion import _anchor
    texts = page.get("texts") or []
    for cand in (page.get("selection") or "", " ".join((page.get("passage") or "").split()[:30])):
        if cand:
            quote, at = _anchor(texts, cand)
            if quote:
                return quote, at
    return "", -1


def reset() -> None:
    _pages.clear()
