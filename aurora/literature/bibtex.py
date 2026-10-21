"""BibTeX out (a project's papers, the report's references) and in (an exported library)."""

from __future__ import annotations

import re
from typing import Any

from aurora.literature.check import Reference
from aurora.literature.records import Paper


def _esc(text: str) -> str:
    return (text or "").replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}").replace("&", "\\&")


def entry(key: str, p: Paper) -> str:
    kind = "misc" if (p.arxiv_id and not p.doi) else "article"
    fields: list[tuple[str, str]] = [
        ("title", "{" + _esc(p.title) + "}"),
        ("author", " and ".join(_esc(a) for a in p.authors) or "Unknown"),
        ("year", str(p.year or "")),
    ]
    if p.venue and kind == "article":
        fields.append(("journal", _esc(p.venue)))
    if p.doi:
        fields.append(("doi", p.doi))
    if p.arxiv_id:
        fields += [("eprint", p.arxiv_id), ("archivePrefix", "arXiv")]
    if p.url:
        fields.append(("url", p.url))
    body = ",\n".join(f"  {k} = {{{v}}}" for k, v in fields if v)
    return f"@{kind}{{{key},\n{body}\n}}\n"


def to_bibtex(entries: list[dict[str, Any]]) -> str:
    """``entries``: ``[{key, paper}]`` as the library returns them."""
    return "\n".join(entry(e["key"], e["paper"]) for e in entries)


_ENTRY = re.compile(r"@(\w+)\s*\{\s*([^,\s]*)\s*,", re.S)


def _fields(body: str) -> dict[str, str]:
    """``name = {value}`` / ``"value"`` / ``number`` pairs, with nested braces."""
    out: dict[str, str] = {}
    i, n = 0, len(body)
    while i < n:
        m = re.compile(r"\s*,?\s*([A-Za-z][\w\-]*)\s*=\s*").match(body, i)
        if not m:
            break
        name, i = m.group(1).lower(), m.end()
        if i < n and body[i] == "{":
            depth, j = 0, i
            while j < n:
                depth += {"{": 1, "}": -1}.get(body[j], 0)
                if depth == 0:
                    break
                j += 1
            value, i = body[i + 1:j], j + 1
        elif i < n and body[i] == '"':
            j = body.find('"', i + 1)
            j = n if j < 0 else j
            value, i = body[i + 1:j], j + 1
        else:
            m2 = re.compile(r"[^,}\s]+").match(body, i)
            value, i = (m2.group(0), m2.end()) if m2 else ("", i + 1)
        out[name] = " ".join(value.replace("{", "").replace("}", "").split())
    return out


def parse(text: str) -> list[tuple[str, Reference]]:
    """The entries of a .bib file as references to check: ``[(citation key, Reference)]``."""
    out = []
    starts = [m for m in _ENTRY.finditer(text or "") if m.group(1).lower() not in ("comment", "string", "preamble")]
    for k, m in enumerate(starts):
        end = starts[k + 1].start() if k + 1 < len(starts) else len(text)
        f = _fields(text[m.end():end].rsplit("}", 1)[0])
        year = re.search(r"(1[89]|20)\d{2}", f.get("year", "") or f.get("date", ""))
        authors = " and ".join(a.strip() for a in f.get("author", "").split(" and "))
        arxiv = f.get("eprint", "") if (f.get("archiveprefix", "").lower() == "arxiv" or "arxiv" in f.get("journal", "").lower()) else ""
        ref = Reference(title=f.get("title", ""), authors=authors.split(" and ")[0] if authors else "",
                        year=int(year.group(0)) if year else None, doi=f.get("doi", ""), arxiv_id=arxiv,
                        text=f"@{m.group(1)}{{{m.group(2)}}}")
        out.append((m.group(2), ref))
    return out
