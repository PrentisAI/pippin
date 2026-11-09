"""What you kept while reading, in your own knowledge base: an Obsidian vault or a Notion workspace.

One note per paper or page, written from what you kept in Pippin's reader: your own notes (the passage, your words,
the replies under them), and Pippin's and the agents' notes you kept. Suggestions you did not keep stay in Pippin.

* **Obsidian**: a Markdown file in the vault the console reads (Notebook → Your notes), in its ``Pippin/`` folder,
  named after the paper. Pippin's part sits between two markers; what you write outside them is left as it is.
* **Notion**: a page under the page you chose, with an internal integration's token (notion.so/my-integrations; share
  that page with the integration). Its content is replaced by what you kept each time it is sent.

Each follows its switch under *What Pippin may write* (``obsidian.create``, ``notion.write``): off, Pippin gives the
Markdown to copy; *ask*, it writes when you press Send; *auto*, also a little after you keep or change a note. Every
write is logged.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

TARGETS = ("obsidian", "notion")
WRITE_KEY = {"obsidian": "obsidian.create", "notion": "notion.write"}
FOLDER = "Pippin"
START, END = "<!-- pippin:start -->", "<!-- pippin:end -->"
NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
PREF_NOTION = "notion"
PREF_SENT = "kb_sent"        # {target: {paper_id: {"at", "ref"}}}: where each paper's note went


# ---------------------------------------------------------------------------
# The note


def _kept(owner: str, paper_id: str) -> list[dict[str, Any]]:
    from aurora.literature.annotations import annotations
    kept = [a for a in annotations.of_owner(owner)
            if a["paper_id"] == paper_id and a["state"] == "kept" and not a.get("deleted_at")]
    for a in kept:
        a["replies"] = annotations.replies(a["ann_id"])
    return kept


def _console_link(paper_id: str) -> str:
    from aurora.im import _console
    try:
        base = _console().rstrip("/")
    except Exception:
        return ""
    return f"{base}/?read={paper_id}" if base.startswith("http") else ""


def note(owner: str, paper_id: str) -> dict[str, Any]:
    """The note for a paper: ``{title, markdown, count}``."""
    from aurora.literature.library import library
    entry = library.paper(paper_id)
    if entry is None:
        raise ValueError("no such paper")
    p = entry["paper"]
    kept = sorted(_kept(owner, paper_id), key=lambda a: (a["page_index"], a["sort_index"], a["created_at"]))
    lines = [f"# {p.title}", ""]
    who = ", ".join(p.authors[:6]) + (" et al." if len(p.authors) > 6 else "")
    meta = [x for x in (who, str(p.year or ""), p.venue) if x]
    if meta:
        lines.append(" · ".join(meta))
    where = (f"https://doi.org/{p.doi}" if p.doi else f"https://arxiv.org/abs/{p.arxiv_id}" if p.arxiv_id else p.url)
    if where:
        lines.append(f"[{where}]({where})")
    lines.append("")
    mine = [a for a in kept if not a["author"]]
    theirs = [a for a in kept if a["author"]]
    if not kept:
        lines.append("_Nothing kept from it yet._")

    def block(a: dict[str, Any]) -> list[str]:
        out = []
        page = f" (p. {a['page_label']})" if a.get("page_label") and not p.is_page else ""
        if a["text"]:
            out += [f"> {' '.join(a['text'].split())}{page}", ""]      # a blank line, or the comment joins the quote
        if a["comment"]:
            label = "" if not a["author"] else f"**{a['author']}{' · ' + a['kind'] if a.get('kind') else ''}:** "
            out.append(label + " ".join(a["comment"].split()))
        for r in a.get("replies") or []:
            if r.get("text") and not r.get("pending"):
                name = r["who"] if r["who"] in ("Librarian", "Skeptic", "Methods", "Pippin") else "You"
                out.append(f"- **{name}:** {' '.join(r['text'].split())}")
        return out + [""]

    if mine:
        lines += ["## My notes", ""]
        for a in mine:
            lines += block(a)
    if theirs:
        lines += ["## Kept from Pippin and the agents", ""]
        for a in theirs:
            lines += block(a)
    link = _console_link(paper_id)
    lines.append(f"_From Pippin{f' · [open it there]({link})' if link else ''} · {time.strftime('%Y-%m-%d %H:%M')}_")
    return {"title": p.title, "markdown": "\n".join(lines).strip() + "\n", "count": len(kept), "key": entry["key"]}


# ---------------------------------------------------------------------------
# Where it can go


def notion_settings(owner: str) -> dict[str, Any]:
    from aurora.auth.secrets import key_status
    from aurora.auth.store import auth_store
    s = auth_store.get_pref(owner, PREF_NOTION, None) or {}
    st = key_status(owner, "notion")
    return {"page": s.get("page", ""), "page_title": s.get("page_title", ""), "token": bool(st.get("set")),
            "hint": st.get("hint", "")}


def page_id_of(text: str) -> str:
    """A Notion page's ID from its link or the ID itself (32 hex digits, dashes or not)."""
    m = re.findall(r"[0-9a-f]{32}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", text.lower())
    return m[-1].replace("-", "") if m else ""


async def connect_notion(owner: str, token: str, page: str) -> dict[str, Any]:
    """Keep the token (encrypted) and the page notes go under, after checking the integration can see that page."""
    from aurora.auth.secrets import get_key, put_key
    from aurora.auth.store import auth_store
    pid = page_id_of(page)
    if not pid:
        raise ValueError("Paste the link of the Notion page your notes should go under")
    token = token.strip() or (get_key(owner, "notion") or "")
    if not token:
        raise ValueError("Paste the integration's token (notion.so/my-integrations → your integration → Secret)")
    import httpx
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(f"{NOTION_API}/pages/{pid}", headers=_headers(token))
    if r.status_code == 401:
        raise ValueError("Notion did not accept that token")
    if r.status_code == 404:
        raise ValueError("The integration cannot see that page: in Notion, open the page → ⋯ → Connections → add "
                         "your integration")
    r.raise_for_status()
    title = _title_of(r.json())
    put_key(owner, "notion", token)
    auth_store.set_pref(owner, PREF_NOTION, {"page": pid, "page_title": title})
    return notion_settings(owner)


def disconnect_notion(owner: str) -> None:
    from aurora.auth.store import auth_store
    auth_store.delete_secret(owner, "notion")
    auth_store.set_pref(owner, PREF_NOTION, {})


def status(owner: str) -> dict[str, Any]:
    """Each target: whether it is set up, its write switch, and why not when it cannot be written."""
    from aurora.auth.writes import level
    from aurora.memory import vault
    v = vault.settings(owner)
    vault_ok = bool(v["path"]) and Path(v["path"]).expanduser().is_dir()
    n = notion_settings(owner)
    return {
        "obsidian": {"ready": vault_ok, "level": level(owner, WRITE_KEY["obsidian"]),
                     "where": str(Path(v["path"]).expanduser() / FOLDER) if vault_ok else "",
                     "why": "" if vault_ok else "Choose your vault folder under Notebook → Your notes"},
        "notion": {"ready": n["token"] and bool(n["page"]), "level": level(owner, WRITE_KEY["notion"]),
                   "where": n["page_title"], "why": "" if n["token"] and n["page"] else "Connect Notion"},
    }


def _sent(owner: str) -> dict[str, Any]:
    from aurora.auth.store import auth_store
    return auth_store.get_pref(owner, PREF_SENT, None) or {}


def sent_to(owner: str, paper_id: str) -> dict[str, Any]:
    s = _sent(owner)
    return {t: s.get(t, {}).get(paper_id) for t in TARGETS if s.get(t, {}).get(paper_id)}


def _remember(owner: str, target: str, paper_id: str, ref: str) -> None:
    from aurora.auth.store import auth_store
    s = _sent(owner)
    s.setdefault(target, {})[paper_id] = {"at": time.time(), "ref": ref}
    auth_store.set_pref(owner, PREF_SENT, s)


# ---------------------------------------------------------------------------
# Obsidian


def _file_name(title: str, key: str) -> str:
    name = re.sub(r'[\\/:*?"<>|#^\[\]]+', " ", title).strip()
    name = " ".join(name.split())[:90].rstrip(" .")
    return f"{name or key}.md"


def write_obsidian(owner: str, paper_id: str) -> dict[str, Any]:
    from aurora.auth.writes import write_log
    from aurora.memory import vault
    root = Path(vault.settings(owner)["path"]).expanduser()
    if not root.is_dir():
        raise ValueError("Choose your vault folder under Notebook → Your notes first")
    n = note(owner, paper_id)
    before = (sent_to(owner, paper_id).get("obsidian") or {}).get("ref", "")
    path = root / before if before and (root / before).is_file() else root / FOLDER / _file_name(n["title"], n["key"])
    part = f"{START}\n{n['markdown']}{END}"
    created = not path.is_file()
    if created:
        path.parent.mkdir(parents=True, exist_ok=True)
        text = part + "\n"
    else:
        old = path.read_text(encoding="utf-8")
        text = (old[:old.index(START)] + part + old[old.index(END) + len(END):]
                if START in old and END in old else old.rstrip() + "\n\n" + part + "\n")
    path.write_text(text, encoding="utf-8")
    rel = str(path.relative_to(root))
    _remember(owner, "obsidian", paper_id, rel)
    write_log.record(owner, WRITE_KEY["obsidian"], {"paper_id": paper_id, "file": rel, "created": created})
    return {"target": "obsidian", "where": rel, "count": n["count"]}


# ---------------------------------------------------------------------------
# Notion


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Notion-Version": NOTION_VERSION, "Content-Type": "application/json"}


def _title_of(page: dict[str, Any]) -> str:
    for prop in (page.get("properties") or {}).values():
        if prop.get("type") == "title":
            return "".join(t.get("plain_text", "") for t in prop.get("title", []))
    return ""


def _rich(text: str) -> list[dict[str, Any]]:
    """Notion rich text from a line of Markdown: **bold**, [links](…) and _italics_; at most 2000 characters a piece."""
    out: list[dict[str, Any]] = []
    for part in re.split(r"(\*\*[^*]+\*\*|\[[^\]]+\]\([^)]+\)|_[^_]+_)", text):
        if not part:
            continue
        ann: dict[str, Any] = {}
        link = None
        if part.startswith("**") and part.endswith("**"):
            part, ann = part[2:-2], {"bold": True}
        elif m := re.fullmatch(r"\[([^\]]+)\]\(([^)]+)\)", part):
            part, link = m.group(1), m.group(2)
        elif len(part) > 2 and part.startswith("_") and part.endswith("_"):
            part, ann = part[1:-1], {"italic": True}
        for i in range(0, len(part), 2000):
            piece: dict[str, Any] = {"type": "text", "text": {"content": part[i:i + 2000]}}
            if link and link.startswith("http"):
                piece["text"]["link"] = {"url": link}
            if ann:
                piece["annotations"] = ann
            out.append(piece)
    return out


def blocks(markdown: str) -> list[dict[str, Any]]:
    """Notion blocks for the note's Markdown (headings, quotes, bullets, paragraphs); its title is the page's."""
    out = []
    for line in markdown.splitlines():
        if not line.strip() or line.startswith("# "):
            continue
        kind, text = "paragraph", line
        if line.startswith("## "):
            kind, text = "heading_2", line[3:]
        elif line.startswith("> "):
            kind, text = "quote", line[2:]
        elif line.startswith("- "):
            kind, text = "bulleted_list_item", line[2:]
        out.append({"object": "block", "type": kind, kind: {"rich_text": _rich(text)}})
    return out


async def write_notion(owner: str, paper_id: str) -> dict[str, Any]:
    import httpx
    from aurora.auth.secrets import get_key
    from aurora.auth.writes import write_log
    s = notion_settings(owner)
    token = get_key(owner, "notion")
    if not token or not s["page"]:
        raise ValueError("Connect Notion first")
    n = note(owner, paper_id)
    body = blocks(n["markdown"])
    h = _headers(token)
    page = (sent_to(owner, paper_id).get("notion") or {}).get("ref", "")
    async with httpx.AsyncClient(timeout=30) as c:
        if page:
            r = await c.get(f"{NOTION_API}/pages/{page}", headers=h)
            if r.status_code == 404 or (r.is_success and r.json().get("archived")):
                page = ""           # deleted in Notion: made again
        created = not page
        if created:
            r = await c.post(f"{NOTION_API}/pages", headers=h, json={
                "parent": {"page_id": s["page"]}, "icon": {"type": "emoji", "emoji": "📄"},
                "properties": {"title": {"title": [{"type": "text", "text": {"content": n["title"][:1900]}}]}},
                "children": body[:100]})
            _raise(r)
            page = r.json()["id"].replace("-", "")
            rest = body[100:]
        else:
            # what was there goes, what is kept now comes
            cursor = None
            old: list[str] = []
            while True:
                r = await c.get(f"{NOTION_API}/blocks/{page}/children", headers=h,
                                params={"page_size": 100, **({"start_cursor": cursor} if cursor else {})})
                _raise(r)
                d = r.json()
                old += [b["id"] for b in d.get("results", [])]
                if not d.get("has_more"):
                    break
                cursor = d.get("next_cursor")
            for bid in old:
                _raise(await c.delete(f"{NOTION_API}/blocks/{bid}", headers=h))
            await c.patch(f"{NOTION_API}/pages/{page}", headers=h, json={
                "properties": {"title": {"title": [{"type": "text", "text": {"content": n["title"][:1900]}}]}}})
            rest = body
        for i in range(0, len(rest), 100):
            _raise(await c.patch(f"{NOTION_API}/blocks/{page}/children", headers=h, json={"children": rest[i:i + 100]}))
    _remember(owner, "notion", paper_id, page)
    write_log.record(owner, WRITE_KEY["notion"], {"paper_id": paper_id, "page": page, "created": created})
    return {"target": "notion", "where": f"https://www.notion.so/{page}", "count": n["count"]}


def _raise(r: Any) -> None:
    if r.is_success:
        return
    try:
        msg = r.json().get("message", "")
    except Exception:
        msg = r.text[:200]
    raise ValueError(f"Notion said: {msg or r.status_code}")


async def send(owner: str, paper_id: str, target: str) -> dict[str, Any]:
    if target == "obsidian":
        return await asyncio.to_thread(write_obsidian, owner, paper_id)
    if target == "notion":
        return await write_notion(owner, paper_id)
    raise ValueError("no such target")


# ---------------------------------------------------------------------------
# On its own (auto): a little after notes are kept or changed, once per paper

_pending: dict[tuple[str, str], asyncio.TimerHandle] = {}
_tasks: set[asyncio.Task] = set()
DELAY = 20.0


def soon(owner: str, paper_id: str) -> None:
    """After notes on a paper change: send its note again to every target set to auto that already has it or is
    set up (a paper with nothing kept is not sent)."""
    from aurora.auth.writes import allowed
    targets = [t for t in TARGETS if allowed(owner, WRITE_KEY[t], background=True) and status(owner)[t]["ready"]]
    if not targets:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    key = (owner, paper_id)
    if key in _pending:
        _pending[key].cancel()

    def fire() -> None:
        _pending.pop(key, None)
        task = loop.create_task(_send_all(owner, paper_id, targets))
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)

    _pending[key] = loop.call_later(DELAY, fire)


async def _send_all(owner: str, paper_id: str, targets: list[str]) -> None:
    if not _kept(owner, paper_id) and not sent_to(owner, paper_id):
        return
    for t in targets:
        try:
            await send(owner, paper_id, t)
        except Exception:
            logger.warning("sending the notes on %s to %s failed", paper_id, t, exc_info=True)
