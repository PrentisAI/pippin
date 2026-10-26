"""Brainstorm spaces in the console: conversations with the agents, the idea board, sharing.

A space is a project (``/api/brainstorm/spaces/<project ID>``) or your own global space
(``/api/brainstorm/spaces/global``). Seeing a project's space needs view rights; writing in it
(a conversation, a card, an answer) needs comment rights. What is private stays its owner's.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from aurora.auth.guard import current_user, require, rights
from aurora.brainstorm import agents as ag
from aurora.brainstorm.store import CARD_TYPES, bs_store

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/brainstorm", tags=["brainstorm"])

_tasks: set[asyncio.Task] = set()


def _title(text: str, n: int = 60) -> str:
    """A conversation named after its first words."""
    text = " ".join(text.split())
    return text if len(text) <= n else text[:n].rsplit(" ", 1)[0] + "…"


def _uid() -> str:
    return current_user().user_id


def _name(user_id: str) -> str:
    if user_id in ag.AGENTS:
        return user_id
    me = current_user()
    if user_id == me.user_id:
        return "You"
    if user_id == "local":
        return "You"
    from aurora.auth.store import auth_store
    row = auth_store.get_user(user_id)
    return row["name"] if row else user_id


def _space(space: str) -> tuple[str, str]:
    """(space key, project ID or ''), checking the asking person may see it."""
    if space == "global":
        return f"user:{_uid()}", ""
    if space.startswith("user:"):
        if space != f"user:{_uid()}":
            raise HTTPException(404, "No such space")
        return space, ""
    require(space, "view")
    return space, space


def _may_write(campaign_id: str) -> None:
    if campaign_id:
        require(campaign_id, "comment")


def _sees_card(c: dict[str, Any], uid: str) -> bool:
    return c["owner"] == uid or (c["vis"] == "project" and not c["space"].startswith("user:"))


def _sees_chat(ch: dict[str, Any], uid: str) -> bool:
    return ch["owner"] == uid or (ch["vis"] == "project" and not ch["space"].startswith("user:"))


def _chat(chat_id: str) -> tuple[dict[str, Any], str]:
    ch = bs_store.chat(chat_id)
    if ch is None:
        raise HTTPException(404, "No such brainstorm")
    _, cid = _space(ch["space"])
    if not _sees_chat(ch, _uid()):
        raise HTTPException(404, "No such brainstorm")
    return ch, cid


def _card(card_id: str) -> tuple[dict[str, Any], str]:
    c = bs_store.card(card_id)
    if c is None:
        raise HTTPException(404, "No such card")
    _, cid = _space(c["space"])
    if not _sees_card(c, _uid()):
        raise HTTPException(404, "No such card")
    return c, cid


def _card_view(c: dict[str, Any]) -> dict[str, Any]:
    out = {**c, "by_name": _name(c["by"]), "owner_name": _name(c["owner"]), "mine": c["owner"] == _uid(),
           "live": bool(c["live"])}
    if c["paper_id"]:
        from aurora.literature.library import library
        entry = library.paper(c["paper_id"])
        if entry:
            p = entry["paper"]
            out["paper"] = {"key": entry["key"], "title": p.title, "byline": p.byline(), "doi": p.doi,
                            "arxiv_id": p.arxiv_id, "url": p.url, "found_in": p.found_in}
    if c["answered_by"]:
        out["answered_by_name"] = _name(c["answered_by"])
    return out


def _chat_view(ch: dict[str, Any]) -> dict[str, Any]:
    return {**ch, "owner_name": _name(ch["owner"]), "mine": ch["owner"] == _uid()}


def _message_view(m: dict[str, Any]) -> dict[str, Any]:
    return {**m, "who_name": _name(m["who"]), "agent": m["who"] in ag.AGENTS, "mine": m["who"] == _uid()}


def _activity() -> str:
    from aurora.auth.store import auth_store
    value = auth_store.get_pref(_uid(), "brainstorm_activity", "moderate")
    return value if value in ag.ACTIVITY else "moderate"


def _access() -> ag.ModelAccess:
    from aurora.api.routes.campaigns import NO_KEY, base_config
    from aurora.config import AuroraConfig
    cfg = AuroraConfig.model_validate({**base_config(), "objective": "brainstorm"})
    key = cfg.api_key_value
    missing = ("set OPENROUTER_API_KEY for the server (in its .env), restart it, then ask again."
               if current_user().is_local else "add yours under the account button → Your API keys, then ask again.")
    return ag.ModelAccess(api_key="" if key in ("", NO_KEY) else key, base_url=cfg.api_base_url,
                          model=cfg.models.brainstorm or cfg.models.frontdesk, missing=missing)


def _literature(space: str, campaign_id: str) -> Any:
    from aurora.api.routes.campaigns import base_config
    from aurora.config import AuroraConfig
    cfg = AuroraConfig.model_validate({**base_config(), "objective": "brainstorm"})
    if not cfg.literature.enabled:
        return None
    from aurora.literature.service import ProjectLiterature
    return ProjectLiterature(campaign_id or space, rule=cfg.literature.add_found)


def _project(campaign_id: str) -> dict[str, Any]:
    if not campaign_id:
        return {}
    from aurora.api.routes.campaigns import _ensure_campaign
    camp = _ensure_campaign(campaign_id)
    runs = [f"{e.run_id} · {e.task_name} · {e.status}" for e in sorted(camp.experiments.values(), key=lambda e: e.started_at)]
    return {"name": camp.name, "objective": camp.objective, "runs": runs}


def _board(ch: dict[str, Any], campaign_id: str) -> ag.Board:
    uid = _uid()
    cards = [c for c in bs_store.cards(ch["space"]) if _sees_card(c, uid)]
    if ch["vis"] == "project":
        usable = [c for c in cards if c["vis"] == "project"]
        private = [c for c in cards if c["vis"] != "project" and c["owner"] == uid and c["type"] == "idea"]
    else:
        usable, private = cards, []
    ids = {c["card_id"] for c in usable}
    from aurora.api.routes.review import latest_map
    return ag.Board(space=ch["space"], viewer=uid, viewer_name=_name(uid) if _name(uid) != "You" else "The researcher",
                    chat=ch, cards=usable, private=private, links=bs_store.links(ids), project=_project(campaign_id),
                    literature=_literature(ch["space"], campaign_id), map=latest_map(ch["space"], uid))


def _attached(attachments: list[dict[str, Any]], campaign_id: str) -> str:
    """What a message brings along, as text for the agents: papers (with notes) and files (their text)."""
    from aurora.literature.library import library
    from aurora.literature.service import paper_card
    parts = []
    for a in attachments[:10]:
        if a.get("kind") == "paper":
            entry = library.paper(str(a.get("paper_id", "")))
            if entry:
                notes = library.notes(campaign_id, [entry["paper_id"]]).get(entry["paper_id"], []) if campaign_id else []
                parts.append(paper_card({**entry, "notes": notes}, abstract_words=200))
        elif a.get("kind") == "file" and campaign_id:
            from aurora.api.routes.files import _inside, clean_rel, project_files_dir
            try:
                path = _inside(project_files_dir(campaign_id), clean_rel(str(a.get("path", ""))))
            except HTTPException:
                continue
            if not path.is_file():
                continue
            if path.suffix.lower() == ".pdf":
                try:
                    from aurora.literature.pdfs import _text
                    text, _ = _text(path.read_bytes(), pages=4)
                except Exception:
                    text = ""
            else:
                text = path.read_text(errors="replace")[:20000]
            parts.append(f"File {a['path']}:\n{text[:8000]}")
    return "\n\n".join(parts)


def _run_agents(ch: dict[str, Any], campaign_id: str, names: list[str], question: str, focus: str, *,
                deep: bool = False, attachments: list[dict[str, Any]] | None = None) -> None:
    """Answer in the background, one agent after the other (each sees the previous answers)."""
    if not names:
        return
    access = _access()
    if campaign_id:
        from aurora.auth.providers import model_problem
        problem = model_problem(access.model, campaign_id)
        if problem:
            bs_store.add_message(ch["chat_id"], names[0], f"I cannot answer here: {problem} An admin can choose "
                                 "another model for the brainstorm agents (models.brainstorm) under Settings.")
            return
    context = _attached(attachments or [], campaign_id)

    async def go() -> None:
        for name in names:
            board = _board(bs_store.chat(ch["chat_id"]) or ch, campaign_id)
            history = bs_store.messages(ch["chat_id"])
            await ag.answer(name, board, history, question, access, focus=focus, deep=deep, context=context)

    task = asyncio.create_task(go())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


# ---------------------------------------------------------------------------
# Spaces


@router.get("/spaces")
async def list_spaces() -> list[dict[str, Any]]:
    from aurora.api.routes.campaigns import _ensure_campaign, store
    from aurora.auth.guard import visible
    me = current_user()
    out = [{"space": "global", "name": "Your own space", "project": False, "can_write": True}]
    for cid in visible(me, [row["campaign_id"] for row in store.list_campaigns()]):
        try:
            out.append({"space": cid, "name": _ensure_campaign(cid).name, "project": True,
                        "can_write": "comment" in rights(me, cid)})
        except Exception:
            continue
    return out


@router.get("/spaces/{space}")
async def get_space(space: str) -> dict[str, Any]:
    key, cid = _space(space)
    uid = _uid()
    cards = [c for c in bs_store.cards(key) if _sees_card(c, uid)]
    chats = [_chat_view(ch) for ch in bs_store.chats(key) if _sees_chat(ch, uid)]
    name = "Your own space"
    members: list[dict[str, Any]] = []
    if cid:
        from aurora.api.routes.campaigns import _ensure_campaign
        name = _ensure_campaign(cid).name
        from aurora.auth.guard import accounts_on
        if accounts_on():
            from aurora.auth.store import auth_store
            for m in auth_store.members(cid):
                if m["principal"].startswith("user:"):
                    row = auth_store.get_user(m["principal"][5:])
                    if row:
                        members.append({"user_id": row["user_id"], "name": row["name"]})
    threads = bs_store.thread_counts({c["card_id"] for c in cards})
    runs = 0
    papers = 0
    if cid:
        from aurora.api.routes.campaigns import _ensure_campaign as _camp
        runs = len(_camp(cid).experiments)
    from aurora.literature.library import library
    papers = len(library.items(cid)) if cid else len(library.items("", owner=uid))
    return {"space": space, "key": key, "name": name, "project": bool(cid), "runs": runs, "papers": papers,
            "threads": threads,
            "can_write": not cid or "comment" in rights(current_user(), cid),
            "chats": chats, "cards": [_card_view(c) for c in cards],
            "links": bs_store.links({c["card_id"] for c in cards}), "activity": _activity(), "members": members,
            "suggestions": [x for x in bs_store.suggestions(key, uid)
                            if all(bs_store.card(cid_) and _sees_card(bs_store.card(cid_), uid) for cid_ in x["cards"]
                                   if cid_.startswith("c_"))][:8],
            "agents": ag.AGENTS}


class NewChat(BaseModel):
    title: str = ""


@router.post("/spaces/{space}/chats")
async def new_chat(space: str, body: NewChat) -> dict[str, Any]:
    key, cid = _space(space)
    _may_write(cid)
    ch = bs_store.new_chat(key, _uid(), body.title.strip() or "New brainstorm")
    return _chat_view(ch)


# ---------------------------------------------------------------------------
# Conversations


@router.get("/chats/{chat_id}")
async def get_chat(chat_id: str) -> dict[str, Any]:
    ch, _ = _chat(chat_id)
    bs_store.seen(chat_id, _uid())
    msgs = [_message_view(m) for m in bs_store.messages(chat_id, viewer=_uid())]
    here = [{"user_id": u, "name": _name(u)} for u in bs_store.here(chat_id)] if ch["vis"] == "project" else []
    return {"chat": _chat_view(ch), "messages": msgs, "pending": any(m["pending"] for m in msgs), "here": here}


class ChatChange(BaseModel):
    title: str | None = None
    focus: str | None = None


@router.patch("/chats/{chat_id}")
async def change_chat(chat_id: str, body: ChatChange) -> dict[str, Any]:
    ch, cid = _chat(chat_id)
    if body.title is not None:
        if ch["owner"] != _uid():
            raise HTTPException(403, "Only its owner renames a brainstorm")
        bs_store.update_chat(chat_id, title=body.title.strip()[:200] or ch["title"])
    if body.focus is not None:
        _may_write(cid)
        bs_store.update_chat(chat_id, focus=body.focus)
    return _chat_view(bs_store.chat(chat_id))  # type: ignore[arg-type]


@router.delete("/chats/{chat_id}")
async def delete_chat(chat_id: str) -> dict[str, Any]:
    ch, _ = _chat(chat_id)
    if ch["owner"] != _uid():
        raise HTTPException(403, "Only its owner deletes a brainstorm")
    bs_store.delete_chat(chat_id)
    return {"ok": True}


class Say(BaseModel):
    text: str
    focus: str = ""          # the idea the message is about
    attachments: list[dict[str, Any]] = []     # [{kind: paper, paper_id} | {kind: file, path}]


@router.post("/chats/{chat_id}/messages")
async def say(chat_id: str, body: Say) -> dict[str, Any]:
    """Post a message; the agents named (or, by your setting, the ones it calls for) answer in the background."""
    ch, cid = _chat(chat_id)
    _may_write(cid)
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "The message is empty")
    if ch["run_id"]:
        return _speak_in_meeting(ch, cid, text)
    earlier = bs_store.messages(chat_id)
    # a deep read waits for the researcher's guess at the authors' path: this message is it
    waiting = next((m for m in reversed(earlier) if (m.get("widget") or {}).get("type") == "guess"
                    and m["widget"].get("state") == "waiting"), None)
    if waiting and _guess_owner(waiting["widget"]["read_id"]):
        msg = bs_store.add_message(chat_id, _uid(), text)
        bs_store.update_message(waiting["msg_id"], widget={**waiting["widget"], "state": "answered"})
        from aurora.api.routes.review import reveal
        reveal(waiting["widget"]["read_id"], chat_id, guess=text)
        return {"message": _message_view(msg), "agents": []}
    opening = not any(m["kind"] == "text" for m in earlier)
    last_agent = next((m["who"] for m in reversed(earlier) if m["who"] in ag.AGENTS), "Librarian")
    msg = bs_store.add_message(chat_id, _uid(), text, attachments=body.attachments[:10])
    if body.focus:
        bs_store.update_chat(chat_id, focus=body.focus)
    if ch["title"] == "New brainstorm" and ch["owner"] == _uid():
        bs_store.update_chat(chat_id, title=_title(text))
    activity = _activity()
    names = ag.mentioned(text) or await ag.pick(text, activity, _access(), opening=opening, fallback=last_agent)
    if body.attachments and not names:       # something brought along is meant to be looked at
        names = ["Librarian"]
    if not names:
        _say_why_quiet(chat_id, activity)
    _run_agents(ch, cid, names, text, body.focus or ch["focus"], attachments=body.attachments)
    return {"message": _message_view(msg), "agents": names}


def _guess_owner(read_id: str) -> bool:
    from aurora.review.store import review_store
    r = review_store.read_by_id(read_id)
    return r is not None and r["owner"] == _uid()


class Act(BaseModel):
    choice: str          # start | decline | skip | resume | an option's id


@router.post("/messages/{msg_id}/act")
async def act(msg_id: str, body: Act) -> dict[str, Any]:
    """Act on a message: start a proposed map or deep read, apply an answer to a challenge, skip a guess, resume."""
    from aurora.api.routes import review as rv
    m = bs_store.message(msg_id)
    if m is None or not m.get("widget"):
        raise HTTPException(404, "Nothing to act on")
    ch, cid = _chat(m["chat_id"])
    _may_write(cid)
    w = dict(m["widget"])
    kind = w.get("type")
    if kind == "proposal":
        if w.get("state") != "pending":
            raise HTTPException(409, "Already decided")
        if body.choice == "decline":
            w["state"] = "declined"
        elif w.get("action") == "map_field":
            started = rv.start_map(ch["space"] if not ch["space"].startswith("user:") else "global", w["topic"],
                                   w.get("brief", ""), chat_id=ch["chat_id"], size=int(w.get("size") or 30))
            w.update(state="started", map_id=started["map_id"])
        elif w.get("action") == "deep_read":
            r = await rv.start_read(w["paper_id"], ch["space"] if not ch["space"].startswith("user:") else "global",
                                    chat_id=ch["chat_id"])
            w.update(state="started", read_id=r["read_id"])
    elif kind == "challenge":
        if w.get("state") != "pending":
            raise HTTPException(409, "Already decided")
        rv._start(w["map_id"], lambda b: rv.fieldmap.apply_challenge(b, w["challenge_id"], body.choice))
        w.update(state="applied", chosen=body.choice)
    elif kind == "guess":
        if w.get("state") != "waiting" or not _guess_owner(w["read_id"]):
            raise HTTPException(409, "Already answered")
        w["state"] = "answered"
        rv.reveal(w["read_id"], ch["chat_id"])
    elif kind == "map_failed":
        rv._start(w["map_id"], lambda b: rv.fieldmap.run(b, start=rv.fieldmap.resume_from(b.content)))
        w["state"] = "resumed"
    else:
        raise HTTPException(400, "Nothing to do with this message")
    bs_store.update_message(msg_id, widget=w)
    return _message_view(bs_store.message(msg_id))  # type: ignore[arg-type]


_QUIET = {
    "off": "No agent answers unless you ask (How active: only when asked). Name one — @Skeptic, @Methods, "
           "@Librarian — or use the buttons below.",
    "moderate": "No agent answered: this read like thinking out loud. To hear from one, name it — @Skeptic, "
                "@Methods, @Librarian — or use the buttons below.",
    "high": "No agent had something to add. Name one — @Skeptic, @Methods, @Librarian — to ask it anyway.",
}


def _say_why_quiet(chat_id: str, activity: str) -> None:
    """Silence explained, to the writer only, and not twice in a row."""
    last = (bs_store.messages(chat_id, viewer=_uid()) or [None])[-2:]
    if any(m and m["kind"] == "system" and m.get("only_for") == _uid() and m["text"] in _QUIET.values() for m in last):
        return
    bs_store.add_message(chat_id, "system", _QUIET.get(activity, _QUIET["moderate"]), kind="system", only_for=_uid())


def _speak_in_meeting(ch: dict[str, Any], campaign_id: str, text: str) -> dict[str, Any]:
    """The researcher speaks in a live round table: the meeting hears it before the next turn."""
    require(campaign_id, "run")          # it steers a run
    from aurora.api.routes.campaigns import _ensure_campaign
    run = _ensure_campaign(campaign_id).experiments.get(ch["run_id"])
    engine = getattr(run, "engine", None)
    name = _name(_uid())
    if engine is None or not engine.meeting_say("the researcher" if name == "You" else name, text):
        raise HTTPException(409, "The round table is not meeting now (it has finished, or the run is not running)")
    msg = bs_store.add_message(ch["chat_id"], _uid(), text)
    bs_store.add_message(ch["chat_id"], "Chair", "Noted. You have the floor before the next turn.", kind="system")
    return {"message": _message_view(msg), "agents": []}


class Deep(BaseModel):
    text: str = ""           # what to look into; empty: the idea under discussion
    focus: str = ""


@router.post("/chats/{chat_id}/deep")
async def deep_search(chat_id: str, body: Deep) -> dict[str, Any]:
    """The Librarian's thorough search: many searches from different angles, then an overview."""
    ch, cid = _chat(chat_id)
    _may_write(cid)
    focus = body.focus or ch["focus"]
    card = bs_store.card(focus) if focus else None
    topic = body.text.strip() or (card["text"] if card else "")
    if not topic:
        raise HTTPException(400, "Say what to search for, or pick an idea")
    text = f"@Librarian deep search: {topic[:300]}"
    msg = bs_store.add_message(chat_id, _uid(), text)
    _run_agents(ch, cid, ["Librarian"], f"Do a deep search on: {topic}", focus, deep=True)
    return {"message": _message_view(msg), "agents": ["Librarian"]}


class Ask(BaseModel):
    agent: str
    card_id: str


@router.post("/chats/{chat_id}/ask")
async def ask(chat_id: str, body: Ask) -> dict[str, Any]:
    """A quick question to one agent about an idea (the buttons under the conversation)."""
    ch, cid = _chat(chat_id)
    _may_write(cid)
    if body.agent not in ag.AGENTS:
        raise HTTPException(400, f"agent must be one of {list(ag.AGENTS)}")
    card, _ = _card(body.card_id)
    if ch["vis"] == "project" and card["vis"] != "project":
        raise HTTPException(400, "Share the idea with the project before discussing it in a shared brainstorm")
    what = {"Skeptic": "what could be wrong with", "Librarian": "what evidence is there for",
            "Methods": "how would we test"}[body.agent]
    text = f"@{body.agent} {what} “{card['text'][:200]}”?"
    msg = bs_store.add_message(chat_id, _uid(), text)
    bs_store.update_chat(chat_id, focus=card["card_id"])
    _run_agents(ch, cid, [body.agent], text, card["card_id"])
    return {"message": _message_view(msg), "agents": [body.agent]}


class Share(BaseModel):
    what: str = "all"            # all: the conversation and its ideas | ideas: only the ideas listed
    card_ids: list[str] = []


@router.post("/chats/{chat_id}/share")
async def share_chat(chat_id: str, body: Share) -> dict[str, Any]:
    ch, cid = _chat(chat_id)
    if not cid:
        raise HTTPException(400, "Your own space is only yours; move an idea to a project to share it")
    if ch["owner"] != _uid():
        raise HTTPException(403, "Only its owner shares a brainstorm")
    _may_write(cid)
    shared = 0
    for card_id in body.card_ids:
        c = bs_store.card(card_id)
        if c and c["owner"] == _uid() and c["space"] == ch["space"] and c["vis"] != "project":
            bs_store.update_card(card_id, vis="project")
            shared += 1
    if body.what == "all":
        bs_store.update_chat(chat_id, vis="project")
    return {"chat": _chat_view(bs_store.chat(chat_id)), "shared_cards": shared}  # type: ignore[arg-type]


class Consent(BaseModel):
    share: bool


@router.post("/messages/{msg_id}/consent")
async def consent(msg_id: str, body: Consent) -> dict[str, Any]:
    """Answer an agent asking whether your private idea may be used in a shared brainstorm."""
    m = bs_store.message(msg_id)
    if m is None or m["only_for"] != _uid() or m["kind"] != "consent":
        raise HTTPException(404, "No such question")
    if m["chat_id"].startswith("card:"):         # asked in an idea's own discussion
        card, cid = _card(m["chat_id"][5:])
        ch = _thread(card)
    else:
        ch, cid = _chat(m["chat_id"])
    if body.share:
        bs_store.update_card(m["consent"], vis="project")
        bs_store.update_message(msg_id, decided="You shared it. Everyone here can now see the idea, and the agents can use it.")
        card = bs_store.card(m["consent"])
        if card:
            bs_store.add_message(m["chat_id"], m["who"], f"{_name(_uid()) if _name(_uid()) != 'You' else 'The researcher'} "
                                 f"shared the idea “{card['text'][:120]}”.", kind="system")
            _run_agents(ch, cid, [m["who"]], f"Now use the idea {card['card_id']} that was just shared.", card["card_id"])
    else:
        bs_store.update_message(msg_id, decided="Kept private. The agents will not use it here.")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Cards


class NewCard(BaseModel):
    type: str = "idea"
    text: str = ""
    paper_id: str = ""                    # material: a paper from the Library
    links: list[dict[str, str]] = []      # [{to, rel}]
    vis: str = "private"
    chat_id: str = ""
    ask_skeptic: bool = False


@router.post("/spaces/{space}/cards")
async def add_card(space: str, body: NewCard) -> dict[str, Any]:
    key, cid = _space(space)
    _may_write(cid)
    if body.type not in CARD_TYPES:
        raise HTTPException(400, "type must be idea, question, note or material")
    vis = "project" if body.vis == "project" and cid else "private"
    if body.type == "material":           # brought from the Library: a real paper, nothing else
        from aurora.literature.library import library
        entry = library.paper(body.paper_id)
        if entry is None:
            raise HTTPException(400, "Bring a paper from the Library")
        text = body.text.strip() or entry["paper"].title
        card = bs_store.add_card(key, _uid(), "material", text, by=_uid(), vis=vis, paper_id=entry["paper_id"],
                                 chat_id=body.chat_id)
    else:
        if not body.text.strip():
            raise HTTPException(400, "The card is empty")
        card = bs_store.add_card(key, _uid(), body.type, body.text, by=_uid(), vis=vis, chat_id=body.chat_id)
    for lk in body.links:
        target = bs_store.card(lk.get("to", ""))
        if target and target["space"] == key and _sees_card(target, _uid()):
            bs_store.link(card["card_id"], target["card_id"], lk.get("rel", "related"))
    if body.ask_skeptic and body.type == "idea":
        ch = bs_store.chat(body.chat_id) if body.chat_id else None
        if ch is None or ch["space"] != key:
            ch = bs_store.new_chat(key, _uid(), _title(card["text"]))
        if ch["vis"] == "project" and vis != "project":
            ch = bs_store.new_chat(key, _uid(), _title(card["text"]))
        msg_text = f"@Skeptic take a first look at “{card['text'][:200]}”."
        bs_store.add_message(ch["chat_id"], _uid(), msg_text)
        bs_store.update_chat(ch["chat_id"], focus=card["card_id"])
        _run_agents(ch, cid, ["Skeptic"], msg_text, card["card_id"])
        return {"card": _card_view(card), "chat_id": ch["chat_id"]}
    return {"card": _card_view(card)}


class CardChange(BaseModel):
    text: str | None = None
    answer: str | None = None
    vis: str | None = None


@router.patch("/cards/{card_id}")
async def change_card(card_id: str, body: CardChange) -> dict[str, Any]:
    c, cid = _card(card_id)
    _may_write(cid)
    mine = c["owner"] == _uid()
    if body.text is not None:
        if not mine:
            raise HTTPException(403, "Only its author edits a card")
        bs_store.update_card(card_id, text=body.text.strip()[:4000])
    if body.answer is not None:
        if c["type"] != "question":
            raise HTTPException(400, "Only questions are answered")
        bs_store.update_card(card_id, answer=body.answer.strip()[:2000], answered_by=_uid())
    if body.vis is not None:
        if not mine or not cid:
            raise HTTPException(403, "Only its author shares a card, and only in a project")
        if body.vis not in ("private", "project"):
            raise HTTPException(400, "vis must be private or project")
        bs_store.update_card(card_id, vis=body.vis)
        if body.vis == "project":       # what it rests on goes along (the author's own cards)
            for lk in bs_store.links({x["card_id"] for x in bs_store.cards(c["space"])}):
                if lk["to_card"] == card_id:
                    src = bs_store.card(lk["from_card"])
                    if src and src["owner"] == _uid() and src["type"] in ("material", "question"):
                        bs_store.update_card(src["card_id"], vis="project")
    return _card_view(bs_store.card(card_id))  # type: ignore[arg-type]


@router.delete("/cards/{card_id}")
async def delete_card(card_id: str) -> dict[str, Any]:
    c, cid = _card(card_id)
    if c["owner"] != _uid():
        raise HTTPException(403, "Only its author deletes a card")
    bs_store.delete_card(card_id)
    return {"ok": True}


class Promote(BaseModel):
    ideation: str = "start"        # start: plan from this idea | roundtable: the seats develop it | brainstorm: live
    add_found: str = ""
    campaign_id: str = ""          # for an idea in your own space: the project it goes to


@router.post("/cards/{card_id}/promote")
async def promote(card_id: str, body: Promote) -> dict[str, Any]:
    """Turn an idea into an experiment proposal in the project's chat, with its evidence and open questions."""
    c, cid = _card(card_id)
    if c["type"] != "idea":
        raise HTTPException(400, "Only an idea becomes a proposal")
    target = cid or body.campaign_id
    if not target:
        raise HTTPException(400, "Choose the project the proposal goes to")
    require(target, "run")
    from aurora.api.routes.campaigns import ExperimentProposal, _ensure_campaign, default_ideation_mode
    camp = _ensure_campaign(target)
    cards = {x["card_id"]: x for x in bs_store.cards(c["space"]) if _sees_card(x, _uid())}
    links = bs_store.links(set(cards))
    into = [(lk["rel"], cards[lk["from_card"]]) for lk in links if lk["to_card"] == card_id]

    def lines(rel: str, type_: str | None = None) -> list[str]:
        out = []
        for r, x in into:
            if r == rel and (type_ is None or x["type"] == type_):
                ref = ""
                if x["paper_id"]:
                    from aurora.literature.library import library
                    entry = library.paper(x["paper_id"])
                    ref = f" [{entry['key']}]" if entry else ""
                answer = f" (answered: {x['answer']})" if x["answer"] else ""
                out.append(f"- {x['text']}{ref}{answer}")
        return out

    parts = [c["text"]]
    if c["stand"]:
        parts.append(f"Where it stands: {c['stand']}")
    for title, rows in (("Evidence for", lines("supports")), ("Evidence against", lines("challenges")),
                        ("Questions the plan must answer", lines("asks about", "question")),
                        ("Next steps proposed in the brainstorm", lines("builds on", "idea"))):
        if rows:
            parts.append(f"{title}:\n" + "\n".join(rows))
    parts.append("(From a brainstorm in AURORA. Cite papers by their keys; they are in the project's literature.)")
    instruction = "\n\n".join(parts)
    skip = ["ideation", "screening"] if body.ideation == "start" else []
    params: dict[str, Any] = {"instruction": instruction, "name": c["text"][:60], "based_on": [], "inputs": [],
                              "model": "", "ideation_mode": "roundtable" if body.ideation == "roundtable"
                              else default_ideation_mode()}
    if body.add_found in ("all", "cited", "none"):
        params["add_found"] = body.add_found
    if body.ideation == "brainstorm":        # the round table, live in this space, with you
        params.update({"ideation_mode": "roundtable", "ideation_live": True})
    proposal = ExperimentProposal(params, skip, f"From the brainstorm: {c['text'][:90]}")
    for p in camp.proposals.values():
        if not p.confirmed:
            p.superseded = True
    camp.proposals[proposal.proposal_id] = proposal
    camp.chat_history.append({"role": "assistant", "timestamp": time.time(), "proposal": proposal.to_dict(),
                              "text": "Here is the idea from your brainstorm as an experiment proposal. Its evidence "
                                      "and open questions go along; change anything before you start it."})
    camp.save()
    if c["chat_id"]:
        bs_store.add_message(c["chat_id"], _uid(), f"Turned “{c['text'][:120]}” into a proposal in the project's chat.",
                             kind="system")
    return {"proposal_id": proposal.proposal_id, "campaign_id": target}


# ---------------------------------------------------------------------------
# A card's own discussion, its links, moving it


def _thread(card: dict[str, Any]) -> dict[str, Any]:
    """A card's discussion, as a conversation of its own (it has the card's visibility)."""
    return {"chat_id": f"card:{card['card_id']}", "space": card["space"], "owner": card["owner"],
            "title": card["text"][:60], "vis": card["vis"], "run_id": "", "focus": card["card_id"]}


@router.get("/cards/{card_id}/thread")
async def get_thread(card_id: str) -> dict[str, Any]:
    c, _ = _card(card_id)
    msgs = [_message_view(m) for m in bs_store.messages(f"card:{card_id}", viewer=_uid())]
    links = bs_store.links({x["card_id"] for x in bs_store.cards(c["space"]) if _sees_card(x, _uid())})
    return {"card": _card_view(c), "messages": msgs, "pending": any(m["pending"] for m in msgs),
            "links": [lk for lk in links if card_id in (lk["from_card"], lk["to_card"])]}


@router.post("/cards/{card_id}/thread")
async def reply(card_id: str, body: Say) -> dict[str, Any]:
    """Write in a card's discussion; @named agents (or, by your setting, the one it calls for) answer there."""
    c, cid = _card(card_id)
    _may_write(cid)
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "The message is empty")
    th = _thread(c)
    msg = bs_store.add_message(th["chat_id"], _uid(), text)
    names = ag.mentioned(text) or await ag.pick(text, _activity(), _access())
    _run_agents(th, cid, names, text, card_id)
    return {"message": _message_view(msg), "agents": names}


class Link(BaseModel):
    to: str
    rel: str


@router.post("/cards/{card_id}/links")
async def link_cards(card_id: str, body: Link) -> dict[str, Any]:
    c, cid = _card(card_id)
    _may_write(cid)
    other, _ = _card(body.to)
    if other["space"] != c["space"] or other["card_id"] == card_id:
        raise HTTPException(400, "Link cards of the same space")
    try:
        bs_store.link(card_id, other["card_id"], body.rel)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True}


@router.delete("/cards/{card_id}/links/{to_card}")
async def unlink_cards(card_id: str, to_card: str, rel: str = "") -> dict[str, Any]:
    c, cid = _card(card_id)
    _may_write(cid)
    bs_store.unlink(card_id, to_card, rel or None)
    return {"ok": True}


class Move(BaseModel):
    campaign_id: str = ""           # the project it goes to
    new_project: str = ""           # or: start a new project with this name


@router.post("/cards/{card_id}/move")
async def move_card(card_id: str, body: Move) -> dict[str, Any]:
    """Move an idea from your own space to a project (an existing one, or a new one started from it)."""
    c, cid = _card(card_id)
    if cid or c["owner"] != _uid():
        raise HTTPException(400, "Only ideas in your own space move to a project")
    target = body.campaign_id
    if body.new_project.strip():
        from aurora.api.routes.campaigns import QuickCreateRequest, quick_create_campaign
        target = (await quick_create_campaign(QuickCreateRequest(prompt=body.new_project.strip())))["campaign_id"]
    if not target:
        raise HTTPException(400, "Choose a project, or name a new one")
    require(target, "comment")
    moved = bs_store.move_card(card_id, target, "private")
    from aurora.literature.library import library
    for mid in moved:                 # its papers join the project's literature
        m = bs_store.card(mid)
        if m and m["paper_id"]:
            library.add_item(target, m["paper_id"], source="person", added_by=_name(_uid()))
    return {"campaign_id": target, "moved": len(moved)}


@router.post("/suggestions/{sug_id}/do")
async def do_suggestion(sug_id: str) -> dict[str, Any]:
    """Act on a suggestion: evidence and caveats go onto the idea; others say what to show."""
    sg = bs_store.suggestion(sug_id)
    if sg is None or sg["owner"] != _uid():
        raise HTTPException(404, "No such suggestion")
    _, cid = _space(sg["space"])
    added = ""
    if sg["kind"] in ("evidence", "caveat") and sg["paper_id"] and sg["cards"]:
        _may_write(cid)
        idea, _ = _card(sg["cards"][0])
        from aurora.literature.library import library
        entry = library.paper(sg["paper_id"])
        if entry:
            card = bs_store.add_card(sg["space"], _uid(), "material", entry["paper"].title, by=sg["by"],
                                     vis=idea["vis"], paper_id=entry["paper_id"])
            bs_store.link(card["card_id"], idea["card_id"], sg["rel"] or "supports")
            added = card["card_id"]
    bs_store.set_suggestion(sug_id, "done")
    return {"kind": sg["kind"], "cards": sg["cards"], "added": added}


@router.post("/suggestions/{sug_id}/dismiss")
async def dismiss_suggestion(sug_id: str) -> dict[str, Any]:
    sg = bs_store.suggestion(sug_id)
    if sg is None or sg["owner"] != _uid():
        raise HTTPException(404, "No such suggestion")
    bs_store.set_suggestion(sug_id, "dismissed")
    return {"ok": True}


class Settings(BaseModel):
    activity: str


@router.put("/settings")
async def settings(body: Settings) -> dict[str, Any]:
    if body.activity not in ag.ACTIVITY:
        raise HTTPException(400, f"activity must be one of {list(ag.ACTIVITY)}")
    from aurora.auth.store import auth_store
    auth_store.set_pref(_uid(), "brainstorm_activity", body.activity)
    return {"activity": body.activity}
