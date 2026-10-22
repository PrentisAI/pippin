"""In-console help: the documentation, readable in the console, and answers to "how do I ..." from it.

The documentation is the ``docs/`` of the repository (in an installed package, a copy under
``aurora/_docs``).  A question is answered from the sections that match it best: by the front
desk's model when there is a key, else by showing those sections.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from pathlib import Path
from typing import Any

_PACKAGED = Path(__file__).resolve().parent / "_docs"
_SOURCE = Path(__file__).resolve().parent.parent / "docs"
_WORD = re.compile(r"[a-z0-9][a-z0-9_.-]*")
_STOP = set("a an and are as at be but by can do does for from how i if in into is it its my of on or so that the "
            "then there this to was what when where which who why will with you your".split())


def docs_dir() -> Path | None:
    for d in (_PACKAGED, _SOURCE):
        if d.is_dir() and (d / "index.md").is_file():
            return d
    return None


def _nav_from_mkdocs() -> list[tuple[str, str, str]]:
    """(section, title, path) in the order of mkdocs.yml's nav, when it is there."""
    import yaml
    for f in (_PACKAGED / "mkdocs.yml", _SOURCE.parent / "mkdocs.yml"):
        if not f.is_file():
            continue
        try:
            nav = (yaml.safe_load(f.read_text()) or {}).get("nav") or []
        except yaml.YAMLError:
            return []
        return _walk(nav, "")
    return []


def _walk(items: list, section: str) -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []
    for item in items:
        for title, target in (item.items() if isinstance(item, dict) else []):
            if isinstance(target, list):
                out += _walk(target, title)
            elif isinstance(target, str) and target.endswith(".md"):
                out.append((section, title, target))
    return out


def pages() -> list[dict[str, str]]:
    """Every page of the documentation: path (relative to docs/), title, section."""
    d = docs_dir()
    if d is None:
        return []
    listed = _nav_from_mkdocs()
    seen = {p for _, _, p in listed}
    out = [{"path": p, "title": t, "section": s} for s, t, p in listed if (d / p).is_file()]
    for f in sorted(d.rglob("*.md")):
        rel = f.relative_to(d).as_posix()
        if rel not in seen:
            out.append({"path": rel, "title": _title(f.read_text(errors="replace")) or rel, "section": "More"})
    return out


def _title(text: str) -> str:
    m = re.search(r"^#\s+(.+)$", text, re.M)
    return m.group(1).strip() if m else ""


def page(path: str) -> dict[str, str] | None:
    d = docs_dir()
    if d is None or ".." in path.split("/") or not path.endswith(".md"):
        return None
    f = (d / path).resolve()
    if not f.is_file() or d.resolve() not in f.parents:
        return None
    text = f.read_text(errors="replace")
    return {"path": path, "title": _title(text) or path, "markdown": text}


def asset(name: str) -> Path | None:
    d = docs_dir()
    if d is None or "/" in name or ".." in name:
        return None
    f = d / "assets" / name
    return f if f.is_file() else None


def _sections() -> list[dict[str, Any]]:
    """Every page split at its headings."""
    out = []
    for p in pages():
        doc = page(p["path"])
        if doc is None:
            continue
        heading, buf = p["title"], []
        for line in doc["markdown"].splitlines():
            m = re.match(r"^#{1,3}\s+(.+)$", line)
            if m and buf:
                out.append({"path": p["path"], "page": p["title"], "heading": heading, "text": "\n".join(buf).strip()})
                heading, buf = m.group(1).strip(), []
            elif m:
                heading = m.group(1).strip()
            else:
                buf.append(line)
        if buf:
            out.append({"path": p["path"], "page": p["title"], "heading": heading, "text": "\n".join(buf).strip()})
    return [s for s in out if s["text"]]


def _words(text: str) -> list[str]:
    return [w.strip(".-") for w in _WORD.findall(text.lower()) if w not in _STOP and len(w) > 1]


def search(question: str, k: int = 6) -> list[dict[str, Any]]:
    """The sections that match the question best (BM25 over headings and text)."""
    secs = _sections()
    if not secs:
        return []
    docs = [Counter(_words(s["heading"] + " " + s["heading"] + " " + s["text"])) for s in secs]
    df: Counter = Counter()
    for d in docs:
        df.update(d.keys())
    avg = sum(sum(d.values()) for d in docs) / len(docs)
    q = set(_words(question))
    scored = []
    for s, d in zip(secs, docs, strict=True):
        length = sum(d.values()) or 1
        score = 0.0
        for w in q:
            if w in d:
                idf = math.log(1 + (len(docs) - df[w] + 0.5) / (df[w] + 0.5))
                score += idf * d[w] * 2.2 / (d[w] + 1.2 * (0.25 + 0.75 * length / avg))
        if score > 0:
            scored.append((score, s))
    scored.sort(key=lambda x: -x[0])
    return [{**s, "score": round(sc, 2)} for sc, s in scored[:k]]


SYSTEM = (
    "You answer questions about using AURORA, a system that runs research with AI agents, from its "
    "documentation. Answer only from the sections given; say plainly when they do not cover the question. "
    "Be brief and concrete: where to click or what to type, in steps when there are several. Name the page "
    "a step comes from as [n] after it. Answer in the language of the question."
)


async def answer(question: str, *, api_key: str, base_url: str, model: str) -> dict[str, Any]:
    """An answer from the documentation, with its sources; without a key, just the sources."""
    hits = search(question)
    if api_key and (not hits or not question.isascii()):
        # the documentation is in English: search with the question's English keywords
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        try:
            r = await client.chat.completions.create(
                model=model, max_tokens=60, temperature=0,
                messages=[{"role": "user", "content": "Give English search keywords (only the words) for this question "
                                                      f"about using a research software console:\n{question}"}])
            hits = search((r.choices[0].message.content or "") + " " + question) or hits
        except Exception:
            pass
        finally:
            await client.close()
    sources = [{"n": i + 1, "path": h["path"], "page": h["page"], "heading": h["heading"],
                "excerpt": h["text"][:400]} for i, h in enumerate(hits)]
    if not hits:
        return {"answer": "", "sources": [], "note": "Nothing in the documentation matches that question."}
    if not api_key:
        return {"answer": "", "sources": sources, "note": "These parts of the documentation match your question."}
    context = "\n\n".join(f"[{i + 1}] {h['page']} — {h['heading']}\n{h['text'][:2500]}" for i, h in enumerate(hits))
    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=api_key, base_url=base_url)
    try:
        r = await client.chat.completions.create(
            model=model, max_tokens=700, temperature=0.2,
            messages=[{"role": "system", "content": SYSTEM},
                      {"role": "user", "content": f"Documentation:\n\n{context}\n\nQuestion: {question}"}])
        text = (r.choices[0].message.content or "").strip()
    except Exception as exc:
        return {"answer": "", "sources": sources, "note": f"Could not ask the model ({type(exc).__name__}); "
                                                          "these parts of the documentation match."}
    finally:
        await client.close()
    return {"answer": text, "sources": sources, "note": ""}
