"""Brainstorm spaces in the console: conversations with the agents, the idea board, sharing.

A space is a project (``/api/brainstorm/spaces/<project ID>``) or your own global space
(``/api/brainstorm/spaces/global``). Seeing a project's space needs view rights; writing in it
(a conversation, a card, an answer) needs comment rights. What is private stays its owner's.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from aurora.auth.guard import current_user, require, rights
from aurora.brainstorm import agents as ag
from aurora.brainstorm import ideas, mentor
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
    # where the Mentor's questioning stands is its own business; the console only needs to know it is guiding
    view = {k: v for k, v in ch.items() if k != "guide"}
    return {**view, "guided": mentor.active(ch), "owner_name": _name(ch["owner"]), "mine": ch["owner"] == _uid()}


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
                deep: bool = False, attachments: list[dict[str, Any]] | None = None, conduct: bool = False) -> None:
    """Answer in the background, one agent after the other (each sees the previous answers); ``conduct``: then
    whether another should join unasked (the researcher's own messages)."""
    if not names and not conduct:
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
            if name == "Mentor":
                await _after_mentor(ch["chat_id"], campaign_id, access)
        if conduct:
            try:
                await _conduct(ch["chat_id"], campaign_id, access, names)
            except Exception:
                logger.warning("the conductor failed in %s", ch["chat_id"], exc_info=True)

    task = asyncio.create_task(go())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def _llm_access(access: ag.ModelAccess) -> Any:
    from aurora.review import llm
    return llm.Access(api_key=access.api_key, base_url=access.base_url, model=access.model, missing=access.missing)


async def _conduct(chat_id: str, campaign_id: str, access: ag.ModelAccess, answered: list[str]) -> None:
    """After the researcher spoke and was answered: another agent joins unasked when the conversation calls for it
    (saying why), a map or the round table is proposed, and the idea in focus is worked on quietly."""
    from aurora.brainstorm import background, conductor
    chat = bs_store.chat(chat_id)
    if chat is None or chat["run_id"] or not conductor.ENABLED:
        return
    activity = _activity()
    board = _board(chat, campaign_id)
    d = await conductor.decide(bs_store.messages(chat_id), board, _uid(), activity, access, answered)
    logger.info("conductor in %s (%s): %s", chat_id, activity, d or "nobody joins")
    if d and d["move"] in conductor.JOINERS:
        bs_store.add_message(chat_id, d["move"], f"{d['move']} joins in: {d['why']}", kind="system")
        await ag.answer(d["move"], _board(bs_store.chat(chat_id) or chat, campaign_id), bs_store.messages(chat_id),
                        conductor.JOIN_ASK.format(**d), access, focus=chat["focus"])
    elif d and d["move"] == "map":
        _propose_map(chat_id, d["about"] or chat["title"], why=d["why"])
    elif d and d["move"] == "roundtable":
        bs_store.add_message(chat_id, "Mentor", f"{d['why']} The round table can develop your idea now: several models "
                             "work it into its strongest version, or meet live with you (Round table, above the "
                             "message box).", kind="system")
    # the idea in focus, worked on in its own thread (not in this conversation), within the day's allowance
    idea = bs_store.card(chat["focus"]) if chat["focus"] else None
    if idea is not None and idea["type"] == "idea" and activity in conductor.COOLDOWN \
            and background._budget(_uid(), activity):
        if await background.work_on(idea, _uid(), lambda th: _board(th, campaign_id), access, only="Librarian"):
            background._budget(_uid(), activity, spend=1)


async def _after_mentor(chat_id: str, campaign_id: str, access: ag.ModelAccess) -> None:
    """What a Mentor reply set in motion: the research question written down, the Skeptic as devil's advocate,
    the handoff at the end of a guided dialogue."""
    for todo in mentor.take_pending(chat_id):
        try:
            if todo in ("rq", "rq_candidates"):
                await _write_question(chat_id, access, candidates=todo == "rq_candidates")
            elif todo.startswith("skeptic:"):
                board = _board(bs_store.chat(chat_id), campaign_id)  # type: ignore[arg-type]
                await ag.answer("Skeptic", board, bs_store.messages(chat_id), mentor.SKEPTIC[todo.split(":", 1)[1]],
                                access, focus=board.chat["focus"])
            elif todo == "handoff":
                _handoff(chat_id)
            elif todo == "versions":
                ch = bs_store.chat(chat_id)
                found = await ideas.extract_versions(_llm_access(access), chat_id, _uid())
                if ch is not None:
                    ideas.post_choice(ch, found, card_id=ch["focus"])
        except Exception as exc:
            logger.warning("after the Mentor (%s) in %s failed", todo, chat_id, exc_info=True)
            bs_store.add_message(chat_id, "Mentor", f"I could not write that down: {str(exc)[:200]}", kind="system",
                                 only_for=_uid())


async def _write_question(chat_id: str, access: ag.ModelAccess, *, candidates: bool = False, feedback: str = "") -> None:
    """The research question written down from the conversation, for the researcher to confirm or change."""
    for old in bs_store.messages(chat_id):          # an earlier version waits no longer
        w = old.get("widget") or {}
        if w.get("type") == "question" and w.get("state") == "waiting":
            bs_store.update_message(old["msg_id"], widget={**w, "state": "superseded"})
    rq = await mentor.draft_question(_llm_access(access), chat_id, _uid(), candidates=candidates, feedback=feedback)
    bs_store.add_message(chat_id, "Mentor", mentor.rq_text(rq), kind="action",
                         widget={"type": "question", "kind": rq["kind"], "state": "waiting", "version": rq["version"],
                                 "rq": rq})


def _propose_map(chat_id: str, topic: str, *, why: str = "") -> None:
    """The Librarian proposes to map the field around a topic (one click starts it), unless a map is there."""
    ch = bs_store.chat(chat_id)
    if ch is None:
        return
    from aurora.api.routes.review import latest_map
    if latest_map(ch["space"], _uid()) is not None:
        return
    from aurora.review.converse import estimate_calls
    calls = estimate_calls(30)
    bs_store.add_message(chat_id, "Librarian", f"{why + ' ' if why else ''}I can map the field around it: why it is "
                         f"needed, what has been done, what is not solved yet. About {calls} model calls.",
                         kind="action", widget={"type": "proposal", "action": "map_field", "topic": " ".join(topic.split())[:200],
                                                "brief": "", "size": 30, "calls": calls, "state": "pending"})


def _handoff(chat_id: str) -> None:
    """A guided dialogue ended: its question can be taken to the literature, a map of its field."""
    ch = bs_store.chat(chat_id)
    seed = mentor.scope_seed(ch)
    if ch is None or seed is None:
        return
    from aurora.api.routes.review import latest_map
    if latest_map(ch["space"], _uid()) is not None:
        return
    from aurora.review.converse import estimate_calls
    topic = " ".join(seed["question"].split())[:200]
    calls = estimate_calls(30)
    bs_store.add_message(chat_id, "Librarian", f"Your question is settled. To see what the field has done around it, I "
                         f"can map it, starting from your question and its scope. About {calls} model calls.",
                         kind="action", widget={"type": "proposal", "action": "map_field", "topic": topic,
                                                "brief": "", "size": 30, "calls": calls, "state": "pending"})


def _question_word(ch: dict[str, Any], campaign_id: str, text: str) -> None:
    """The researcher's word on their written-down research question: a change, a yes, or something else."""
    access = _access()

    async def go() -> None:
        try:
            verdict = await _read_word(access, ch["chat_id"], text)
        except Exception:
            logger.info("reading the researcher's word on the question failed", exc_info=True)
            verdict = {"action": "other"}
        if verdict.get("action") == "confirm":
            _settle(ch["chat_id"], campaign_id, pick=verdict.get("pick"))
        elif verdict.get("action") == "revise":
            await _write_question(ch["chat_id"], access, feedback=text,
                                  candidates=(mentor.guide_of(bs_store.chat(ch["chat_id"]) or ch).get("rq") or {}).get("kind") == "candidates")
        else:
            _run_agents(bs_store.chat(ch["chat_id"]) or ch, campaign_id, ["Mentor"], text, ch["focus"])

    task = asyncio.create_task(go())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def _read_word(access: ag.ModelAccess, chat_id: str, text: str) -> dict[str, Any]:
    from aurora.review import llm
    rq = mentor.guide_of(bs_store.chat(chat_id) or {}).get("rq") or {}
    out = await llm.ask(_llm_access(access), "rq_word", f"""A researcher's research question was written down for them:

{mentor.rq_text(rq)}

Their reply: "{text[:2000]}"

Is the reply (a) confirming it as it is ("confirm"; with candidates, which one they take, numbered from 1, as "pick"), (b) asking to change it in some way ("revise"), or (c) about something else, going on with the conversation ("other")?

JSON: {{"action": "confirm|revise|other", "pick": null}}""", max_tokens=600)
    pick = out.get("pick")
    try:
        out["pick"] = int(pick) - 1 if pick is not None else None
    except (TypeError, ValueError):
        out["pick"] = None
    return out


def _settle(chat_id: str, campaign_id: str, *, pick: int | None = None) -> None:
    """The research question confirmed: on the board, in the plan, and the Mentor goes on from it."""
    rq = mentor.settle(chat_id, _uid(), pick=pick)
    for m in bs_store.messages(chat_id):
        w = m.get("widget") or {}
        if w.get("type") == "question" and w.get("state") == "waiting":
            bs_store.update_message(m["msg_id"], widget={**w, "state": "confirmed", "card_id": rq["card_id"]})
    ch = bs_store.chat(chat_id)
    if ch is not None:
        _run_agents(ch, campaign_id, ["Mentor"], f"(Console: the researcher confirmed their research question: {rq['question']})",
                    rq["card_id"])


async def _direction_word(m: dict[str, Any], ch: dict[str, Any], campaign_id: str, i: int, text: str, *,
                          again: bool) -> dict[str, Any]:
    """The researcher's word on the round table's decision: go on with a direction (changed or not), or have the
    seats go another round from it. It answers the run's gate."""
    require(campaign_id, "run")
    w = dict(m["widget"])
    items = w.get("drafts") or []
    if not 0 <= i < len(items):
        raise HTTPException(400, "No such direction")
    item = items[i]
    final = " ".join((text or item["text"]).split())
    from aurora.api.routes.campaigns import _ensure_campaign
    run = _ensure_campaign(campaign_id).experiments.get(w.get("run_id", ""))
    engine = getattr(run, "engine", None)
    gate = next((g for g in (engine.human_gate.pending if engine is not None else []) if g.gate_id == w.get("gate_id")), None)
    if gate is None:
        raise HTTPException(409, "The run no longer waits for this decision")
    edited = " ".join(item["text"].split()) != final
    who = _name(_uid())
    if again:
        action, message = "instruct", final
    elif item.get("chosen") and not edited:
        action, message = "continue", ""
    else:
        whose = "my own idea" if item.get("yours") else f"{item.get('seat', 'a seat')}'s direction from the meeting"
        action, message = "propose", f"Pursue this direction ({whose}{', as I changed it' if edited else ''}): {final}"
    try:
        engine.human_gate.resolve(gate.gate_id, action, message, by=who)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    taken: dict[str, Any] = {"state": "taken", "taken": i, "action": action, "text": final}
    source = bs_store.card(w.get("card_id") or "") if w.get("card_id") else None
    if source is not None and source["owner"] == _uid() and (item.get("yours") or edited or action == "propose"):
        v = ideas.keep(source, final, viewer=_uid(), base="" if item.get("yours") else item["text"],
                       agent=str(item.get("seat", "")), chat_id=ch["chat_id"])
        taken.update(origin=v["origin"], share=v["share"])
    bs_store.update_message(m["msg_id"], widget={**w, **taken})
    bs_store.add_move(ch["chat_id"], ch["space"], "direction", {"action": action, "yours": bool(item.get("yours")),
                                                               "chosen": bool(item.get("chosen")), "edited": edited})
    return _message_view(bs_store.message(m["msg_id"]))  # type: ignore[arg-type]


def mentor_after_map(chat_id: str, map_id: str) -> None:
    """A first map of the field is ready in a conversation: the Mentor asks about its unsolved problems."""
    ch = bs_store.chat(chat_id)
    if ch is None:
        return
    mentor.start(chat_id)
    _run_agents(ch, "" if ch["space"].startswith("user:") else ch["space"], ["Mentor"],
                "(Console: the map of the field is ready, and its problems attempted but not solved well are in what "
                "you see above. Ask the researcher which of them bothers them most, and why.)", ch["focus"])


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


@router.get("/figures/{fig_id}")
async def get_figure(fig_id: str) -> Any:
    """A figure an agent drew in a conversation you can see: a page for a sandboxed frame, allowed only its own script."""
    from fastapi.responses import HTMLResponse

    from aurora.figures import TELL, figures
    f = figures.get(fig_id)
    if f is None:
        raise HTTPException(404, "No such figure")
    if f["chat_id"]:
        _chat(f["chat_id"])
    elif f["owner"] != _uid():
        raise HTTPException(404, "No such figure")
    return HTMLResponse(f["html"] + TELL, headers={
        "Content-Security-Policy": "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                                   "img-src data: blob:; font-src data:; sandbox allow-scripts",
        "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})


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
    checkpoint = next((m for m in reversed(earlier) if (m.get("widget") or {}).get("type") in ("scope", "corpus")
                       and m["widget"].get("state") == "waiting"), None)
    if checkpoint and not ag.mentioned(text) and _map_owner(checkpoint["widget"]["map_id"]):
        # a map waits for the researcher's word on its scope or its papers: this message is it
        msg = bs_store.add_message(chat_id, _uid(), text)
        from aurora.api.routes.review import start_amend
        step = "scope" if checkpoint["widget"]["type"] == "scope" else "screen"
        try:
            start_amend(checkpoint["widget"]["map_id"], step, text)
        except HTTPException as exc:
            bs_store.add_message(chat_id, "system", f"The map could not take that now: {exc.detail}", kind="system",
                                 only_for=_uid())
        return {"message": _message_view(msg), "agents": []}
    asked = next((m for m in reversed(earlier) if (m.get("widget") or {}).get("type") == "question"
                  and m["widget"].get("state") == "waiting"), None)
    if asked and not ag.mentioned(text) and ch["owner"] == _uid():
        # the research question waits for the researcher's word: a change, a yes, or the conversation goes on
        msg = bs_store.add_message(chat_id, _uid(), text)
        _question_word(ch, cid, text)
        return {"message": _message_view(msg), "agents": ["Mentor"]}
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
    if not ag.mentioned(text) and mentor.active(ch) and ch["owner"] == _uid():
        names = ["Mentor"]                  # a guided conversation: the researcher is answering the Mentor
    else:
        names = ag.mentioned(text) or await ag.pick(text, activity, _access(), opening=opening, fallback=last_agent)
    if "Mentor" in names:
        mentor.start(chat_id)
    if body.attachments and not names:       # something brought along is meant to be looked at
        names = ["Librarian"]
    if not names:
        _say_why_quiet(chat_id, activity)
    _run_agents(ch, cid, names, text, body.focus or ch["focus"], attachments=body.attachments, conduct=True)
    return {"message": _message_view(msg), "agents": names}


def _map_owner(map_id: str) -> bool:
    from aurora.review.store import review_store
    m = review_store.map(map_id)
    return m is not None and m["owner"] == _uid()


def _guess_owner(read_id: str) -> bool:
    from aurora.review.store import review_store
    r = review_store.read_by_id(read_id)
    return r is not None and r["owner"] == _uid()


class Act(BaseModel):
    choice: str          # start | decline | skip | resume | an option's id | take:<i> | again:<i> | pick:<i>
    text: str = ""       # a draft as the researcher edited it


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
                                   w.get("brief", ""), chat_id=ch["chat_id"], size=int(w.get("size") or 30),
                                   question=mentor.scope_seed(ch))
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
    elif kind in ("scope", "corpus"):
        if w.get("state") != "waiting" or not _map_owner(w["map_id"]):
            raise HTTPException(409, "Already decided")
        rv.go_on(w["map_id"], "scope" if kind == "scope" else "screen")
        w["state"] = "confirmed"
    elif kind == "question":
        if w.get("state") != "waiting" or ch["owner"] != _uid():
            raise HTTPException(409, "Already decided")
        pick = None
        if body.choice.startswith("take:"):
            try:
                pick = int(body.choice.split(":", 1)[1])
            except ValueError:
                raise HTTPException(400, "take:<number>") from None
        elif body.choice != "confirm":
            raise HTTPException(400, "confirm, or take:<number>")
        try:
            _settle(ch["chat_id"], cid, pick=pick)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        return _message_view(bs_store.message(msg_id))  # type: ignore[arg-type]
    elif kind == "drafts":
        if w.get("state") != "waiting":
            raise HTTPException(409, "Already decided")
        verb, _, n = body.choice.partition(":")
        if verb not in ("take", "again") or not n.isdigit():
            raise HTTPException(400, "take:<number> or again:<number>")
        if w.get("purpose") == "direction":
            return await _direction_word(m, ch, cid, int(n), body.text, again=verb == "again")
        try:
            got = ideas.take(m, int(n), body.text, _uid())
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        bs_store.update_chat(ch["chat_id"], focus=got["card_id"])
        agent = got["agent"] if got["agent"] in ag.AGENTS else "Methods"
        if verb == "again":
            done = bs_store.message(msg_id) or m
            bs_store.update_message(msg_id, widget={**(done.get("widget") or {}), "again": True})
            _run_agents(bs_store.chat(ch["chat_id"]) or ch, cid, [agent],
                        f"(Console: the researcher took draft {int(n) + 1}{' and edited it' if got['origin'] != 'ai' else ''} "
                        f"and asks for another round from it: “{got['text']}”. Call draft_versions with two new drafts that "
                        f"build on their version (it is shown first); vary them in substance.)", got["card_id"])
        elif got["origin"] == "ai":
            # taken as it was: the Mentor asks what in it is theirs (people tend to accept a rewrite without a thought)
            _run_agents(bs_store.chat(ch["chat_id"]) or ch, cid, ["Mentor"],
                        f"(Console: the researcher took the AI's draft without changing it: “{got['text']}”. Ask them, in "
                        f"one question, what in it is most theirs and what they are unsure of.)", got["card_id"])
        return _message_view(bs_store.message(msg_id))  # type: ignore[arg-type]
    elif kind == "choice":
        if w.get("state") != "waiting":
            raise HTTPException(409, "Already decided")
        verb, _, n = body.choice.partition(":")
        if verb != "pick" or not n.isdigit():
            raise HTTPException(400, "pick:<number>")
        try:
            got = ideas.choose(m, int(n), _uid())
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        bs_store.update_chat(ch["chat_id"], focus=got["card_id"])
        mentor.start(ch["chat_id"])
        _run_agents(bs_store.chat(ch["chat_id"]) or ch, cid, ["Mentor"],
                    f"(Console: the researcher picked this version of their idea to go on with: “{got['text']}”. "
                    f"Help them elaborate it.)", got["card_id"])
        return _message_view(bs_store.message(msg_id))  # type: ignore[arg-type]
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
        # nothing typed and no idea picked: what the researcher has been talking about
        said = [m["text"] for m in bs_store.messages(chat_id) if m["who"] == _uid() and m["kind"] == "text" and m["text"]]
        topic = " / ".join(re.sub(r"@\w+\s*", "", t).strip() for t in said[-3:])
    if not topic:
        raise HTTPException(400, "Say what to search for, or pick an idea")
    text = f"@Librarian deep search: {topic[:300]}"
    msg = bs_store.add_message(chat_id, _uid(), text)
    _run_agents(ch, cid, ["Librarian"], f"Do a deep search on: {topic}", focus, deep=True)
    return {"message": _message_view(msg), "agents": ["Librarian"]}


class Guide(BaseModel):
    on: bool


@router.post("/chats/{chat_id}/guide")
async def set_guide(chat_id: str, body: Guide) -> dict[str, Any]:
    """Pause the Mentor's guiding (messages go to whoever fits them) or resume it; what it learned is kept."""
    ch, cid = _chat(chat_id)
    _may_write(cid)
    if ch["owner"] != _uid():
        raise HTTPException(403, "Only the one whose conversation it is sets whether the Mentor guides it")
    if body.on:
        mentor.start(chat_id)
    else:
        g = mentor.guide_of(ch)
        g["on"] = False
        bs_store.set_guide(chat_id, g)
    return {"guided": mentor.active(bs_store.chat(chat_id) or ch)}


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
            "Methods": "how would we test", "Mentor": "help me think through"}[body.agent]
    text = f"@{body.agent} {what} “{card['text'][:200]}”" + ("" if body.agent == "Mentor" else "?")
    msg = bs_store.add_message(chat_id, _uid(), text)
    if body.agent == "Mentor":
        mentor.start(chat_id)
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


@router.get("/chats/{chat_id}/plan")
async def get_plan(chat_id: str) -> dict[str, Any]:
    """The research plan the Mentor's guided conversation builds: the question and the researcher's insights."""
    ch, _ = _chat(chat_id)
    out = mentor.plan(ch)
    card = bs_store.card(ch["focus"]) if ch["focus"] else None
    out["idea"] = ({"card_id": card["card_id"], "text": card["text"], "versions": ideas.history(card["card_id"])}
                   if card is not None and card["type"] == "idea" and _sees_card(card, _uid()) else None)
    return out


class InsightChange(BaseModel):
    text: str | None = None
    section: str | None = None


def _insight(insight_id: str) -> dict[str, Any]:
    i = bs_store.insight(insight_id)
    if i is None:
        raise HTTPException(404, "No such point in the plan")
    _chat(i["chat_id"])
    if i["owner"] != _uid():
        raise HTTPException(403, "Only the researcher whose plan it is changes it")
    return i


@router.patch("/insights/{insight_id}")
async def change_insight(insight_id: str, body: InsightChange) -> dict[str, Any]:
    _insight(insight_id)
    if body.section is not None and body.section not in mentor.SECTIONS:
        raise HTTPException(400, f"section must be one of {list(mentor.SECTIONS)}")
    if body.text is not None and not body.text.strip():
        raise HTTPException(400, "The point is empty")
    bs_store.update_insight(insight_id, text=body.text.strip()[:1500] if body.text else None, section=body.section)
    return bs_store.insight(insight_id)  # type: ignore[return-value]


@router.delete("/insights/{insight_id}")
async def delete_insight(insight_id: str) -> dict[str, Any]:
    _insight(insight_id)
    bs_store.delete_insight(insight_id)
    return {"ok": True}


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
        text = body.text.strip()[:4000]
        if not text:
            raise HTTPException(400, "The card is empty")
        if text != c["text"]:
            if c["type"] == "idea":
                ideas.researcher_edit(c, text, _uid())       # its next version, and how much of it is theirs
                _tell_meeting(c, cid, text)
            else:
                bs_store.update_card(card_id, text=text)
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


def _tell_meeting(card: dict[str, Any], campaign_id: str, text: str) -> None:
    """A live round table that started from this idea hears its new version before the next turn."""
    if not campaign_id:
        return
    from aurora.api.routes.campaigns import _ensure_campaign
    for run in _ensure_campaign(campaign_id).experiments.values():
        engine = getattr(run, "engine", None)
        cfg = getattr(engine, "_config", None)
        if cfg is None or cfg.ideation.source_card != card["card_id"]:
            continue
        name = _name(_uid())
        if engine.meeting_say("the researcher" if name == "You" else name, f"I revised my idea. It is now: {text}"):
            ch = bs_store.chat_for_run(run.run_id)
            if ch is not None:
                bs_store.add_message(ch["chat_id"], "Chair", f"The researcher revised their idea: “{text[:300]}”. The "
                                     "meeting hears it before the next turn.", kind="system")


@router.get("/cards/{card_id}/versions")
async def card_versions(card_id: str) -> list[dict[str, Any]]:
    """An idea's versions: who wrote each, and how much of it is the researcher's."""
    c, _ = _card(card_id)
    return ideas.history(c["card_id"])


@router.get("/spaces/{space}/ideation-metrics")
async def ideation_metrics(space: str) -> dict[str, Any]:
    """For evaluation: the diversity of the space's ideas, the researcher's share of them, and the moves."""
    key, _ = _space(space)
    uid = _uid()
    cards = [c for c in bs_store.cards(key) if c["type"] == "idea" and c["owner"] == uid and not c["live"]]
    from collections import Counter
    moves = bs_store.moves(space=key)
    return {"ideas": len(cards), "diversity": await ideas.diversity(_llm_access(_access()), [c["text"] for c in cards]),
            "ownership": ideas.ownership(cards), "moves": dict(Counter(mv["kind"] for mv in moves)),
            "taken": [mv["data"] for mv in moves if mv["kind"] == "taken"]}


@router.post("/chats/{chat_id}/depth")
async def chat_depth(chat_id: str) -> dict[str, Any]:
    """For evaluation: how far the researcher elaborated their idea in each answer (0 to 5, a few cents)."""
    ch, _ = _chat(chat_id)
    levels = await ideas.elaboration_depth(_llm_access(_access()), ch["chat_id"], ch["owner"])
    worked = [x["level"] for x in levels if x["level"] > 0]
    return {"levels": levels, "mean": round(sum(worked) / len(worked), 2) if worked else None}


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
    parts.append("(From a brainstorm in Pippin. Cite papers by their keys; they are in the project's literature.)")
    instruction = "\n\n".join(parts)
    skip = ["ideation", "screening"] if body.ideation == "start" else []
    params: dict[str, Any] = {"instruction": instruction, "name": c["text"][:60], "based_on": [], "inputs": [],
                              "model": "", "ideation_mode": "roundtable" if body.ideation == "roundtable"
                              else default_ideation_mode()}
    if body.add_found in ("all", "cited", "none"):
        params["add_found"] = body.add_found
    if body.ideation == "brainstorm":        # the round table, live in this space, with you
        params.update({"ideation_mode": "roundtable", "ideation_live": True})
    # the idea it comes from: a live round table hears its new versions, and what the run finds comes back to it
    params["source_card"] = card_id
    if body.ideation in ("roundtable", "brainstorm"):
        # the seats start from your idea (its current version) and develop it, rather than proposing their own
        params["ideation_develop"] = True
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
