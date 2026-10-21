"""Which paper is this PDF? Its DOI or arXiv ID if it shows one, else its title."""

from __future__ import annotations

import io
import re

from aurora.literature.check import Reference
from aurora.literature.records import arxiv_version, clean_arxiv, clean_doi

_ARXIV = re.compile(r"arXiv:\s*(\d{4}\.\d{4,5}(?:v\d+)?)", re.I)
_NOT_TITLES = re.compile(r"^(microsoft word|untitled|document|paper|manuscript|arxiv|pdf|slide|\d+$)", re.I)


def _text(data: bytes, pages: int = 2) -> tuple[str, dict[str, str]]:
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data))
    meta = {k.lstrip("/").lower(): str(v) for k, v in (reader.metadata or {}).items() if v}
    text = "\n".join((p.extract_text() or "") for p in reader.pages[:pages])
    return text, meta


def identify(data: bytes, filename: str = "") -> Reference:
    """What the PDF says it is: a reference to check (DOI and arXiv ID first, then the title)."""
    try:
        text, meta = _text(data)
    except Exception:
        text, meta = "", {}
    head = text[:6000]
    doi = clean_doi(meta.get("doi", "")) or clean_doi(head)
    m = _ARXIV.search(head) or _ARXIV.search(meta.get("subject", ""))
    arxiv = clean_arxiv(m.group(1)) if m else ""
    version = arxiv_version(m.group(1)) if m else 0
    title = meta.get("title", "").strip()
    if not title or len(title) < 15 or _NOT_TITLES.match(title) or title.lower().endswith((".pdf", ".doc", ".docx")):
        # the first substantial lines before the abstract are usually the title
        lines = []
        for line in head.splitlines():
            line = " ".join(line.split())
            if re.match(r"^(abstract|summary|keywords|introduction)\b", line, re.I):
                break
            if len(line) < 12 or re.search(r"(journal|vol\.|doi|http|©|copyright|received|accepted|arxiv)", line, re.I):
                if lines:
                    break
                continue
            lines.append(line)
            if len(" ".join(lines)) > 140:
                break
        title = " ".join(lines[:2]) or re.sub(r"[_-]+", " ", re.sub(r"\.pdf$", "", filename, flags=re.I))
    author = meta.get("author", "").split(";")[0].split(",")[0].strip()
    return Reference(title=title[:300], authors=author if len(author) > 2 else "", doi=doi, arxiv_id=arxiv,
                     arxiv_version=version, text=f"Uploaded PDF {filename}", truncated=True)
