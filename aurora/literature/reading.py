"""A paper's PDF for the reader: where it is, fetching an open one, and its text page by page.

Where a paper's PDF is looked for, first found first:

1. the project's files (``papers/<key>.pdf``, or the Better BibTeX key Zotero gave it);
2. the reader's own Library (``AURORA_HOME/library/<user>/papers``);
3. the other projects the reader can see;
4. the shared cache of open PDFs (``AURORA_HOME/library/_open``), filled from the paper's open-access
   link (arXiv, or the PDF link an index gave) the first time someone reads it.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

MAX_PDF_BYTES = 80 * 1024 * 1024
_texts: dict[tuple[str, float], list[str]] = {}
# the HTTP client used to fetch open PDFs (None: a new one per request); tests put a fake here
http_client: httpx.AsyncClient | None = None


def _home() -> Path:
    from aurora.paths import aurora_home
    return Path(aurora_home())


def _names(paper_id: str) -> list[str]:
    """File stems a paper's PDF may have: its key, and the citation keys Zotero gave it."""
    from aurora.literature.library import library
    from aurora.literature.zotero import zotero_store
    entry = library.paper(paper_id)
    names = [entry["key"]] if entry else []
    ck = zotero_store.citekeys([paper_id]).get(paper_id)
    if ck:
        names.append(re.sub(r"[^A-Za-z0-9_.-]+", "_", ck))
    return names


def _in(folder: Path, names: list[str]) -> Path | None:
    for n in names:
        p = folder / f"{n}.pdf"
        if p.is_file():
            return p
    return None


def project_pdf(campaign_id: str, paper_id: str) -> Path | None:
    from aurora.api.routes.files import project_files_dir
    try:
        return _in(project_files_dir(campaign_id) / "papers", _names(paper_id))
    except Exception:
        return None


def cached_pdf(paper_id: str) -> Path:
    return _home() / "library" / "_open" / f"{paper_id}.pdf"


def find_pdf(paper_id: str, viewer: str, campaign_id: str = "", other_projects: list[str] | None = None) -> Path | None:
    """The PDF the reader may read, or None."""
    names = _names(paper_id)
    if campaign_id and (p := project_pdf(campaign_id, paper_id)):
        return p
    if p := _in(_home() / "library" / viewer / "papers", names):
        return p
    for cid in other_projects or []:
        if cid != campaign_id and (p := project_pdf(cid, paper_id)):
            return p
    cached = cached_pdf(paper_id)
    return cached if cached.is_file() else None


def open_url(paper: Any) -> str:
    """Where an open copy of the paper is: arXiv first, else the PDF link an index gave."""
    if getattr(paper, "arxiv_id", ""):
        return f"https://arxiv.org/pdf/{paper.arxiv_id}"
    return getattr(paper, "pdf_url", "") or ""


async def fetch_open_pdf(paper_id: str) -> Path | None:
    """Fetch the paper's open PDF into the shared cache (once); None when there is none or it failed."""
    from aurora.literature.library import library
    entry = library.paper(paper_id)
    if entry is None:
        return None
    url = open_url(entry["paper"])
    if not url:
        return None
    target = cached_pdf(paper_id)
    if target.is_file():
        return target
    try:
        if http_client is not None:
            r = await http_client.get(url, follow_redirects=True)
        else:
            async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0), follow_redirects=True,
                                         headers={"User-Agent": "AURORA (research reader)"}) as c:
                r = await c.get(url)
    except httpx.HTTPError as exc:
        logger.info("fetching the PDF of %s from %s failed: %s", paper_id, url, exc)
        return None
    if r.status_code != 200 or not r.content.startswith(b"%PDF") or len(r.content) > MAX_PDF_BYTES:
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".part")
    tmp.write_bytes(r.content)
    tmp.replace(target)
    return target


def page_texts(path: Path) -> list[str]:
    """The text of each page (what the agents read; quotes are found in it again)."""
    key = (str(path), path.stat().st_mtime)
    if key in _texts:
        return _texts[key]
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        pages = [(p.extract_text() or "") for p in reader.pages]
    except Exception:
        logger.info("reading the text of %s failed", path, exc_info=True)
        pages = []
    if len(_texts) > 32:
        _texts.clear()
    _texts[key] = pages
    return pages
