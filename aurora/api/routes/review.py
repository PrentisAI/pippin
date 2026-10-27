"""Zoom out and zoom in in the console: maps of a research direction, and deep reads of single papers.

* ``/api/review/spaces/{space}/maps``: the maps of a space (a project, or ``global`` for your own); making one
  starts building it in the background (see :mod:`aurora.review.fieldmap`);
* ``/api/review/maps/{map_id}``: a map, its papers and versions, what its build is doing; and the loops back up
  its chain: accept a proposed revision, choose another framework, place a paper again, challenge the
  definition (its consequences first, then apply an option), look for papers that joined since;
* ``/api/review/reads``: a deep read of a paper (twelve questions, see :mod:`aurora.review.deepread`), your own
  guess before the authors' path is shown, follow-up questions, and sending its idea to the brainstorm.

A map is its owner's until shared with the project; then the project's members can see it. Only its owner
changes it. A deep read is its reader's.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from aurora.auth.guard import current_user, require, rights
from aurora.literature.library import library
from aurora.review import converse, deepread, fieldmap
from aurora.review import llm as rllm
from aurora.review.store import review_store

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/review", tags=["review"])

_running: dict[str, asyncio.Task] = {}
_tasks: set[asyncio.Task] = set()


def _uid() -> str:
    return current_user().user_id


def _space(space: str) -> tuple[str, str]:
    if not space or space == "global" or space == f"user:{_uid()}":
        return f"user:{_uid()}", ""
    if space.startswith("user:"):
        raise HTTPException(404, "No such space")
    require(space, "view")
    return space, space


def _cfg() -> Any:
    from aurora.api.routes.campaigns import base_config
    from aurora.config import AuroraConfig
    return AuroraConfig.model_validate({**base_config(), "objective": "review"})


def _access(campaign_id: str, model: str = "") -> rllm.Access:
    """Access with the review model (``models.review``), or ``model``."""
    from aurora.api.routes.brainstorm import _access as bs_access
    a = bs_access()
    model = model or _cfg().models.review or a.model
    if campaign_id:
        from aurora.auth.providers import model_problem
        if problem := model_problem(model, campaign_id):
            raise HTTPException(400, f"{problem} An admin can choose another model for maps (models.review).")
    if not a.api_key:
        raise HTTPException(400, f"No API key is set: {a.missing}")
    return rllm.Access(api_key=a.api_key, base_url=a.base_url, model=model, missing=a.missing)


def _seat_models() -> list[str]:
    return list(_cfg().models.ideation_seats or [])


def _screeners(campaign_id: str) -> list[rllm.Access]:
    base = _access(campaign_id)
    return [base.with_model(m) for m in (_cfg().models.review_screen or [])[:2]]


def _lit(space: str, campaign_id: str) -> Any:
    from aurora.api.routes.brainstorm import _literature
    lit = _literature(space, campaign_id)
    if lit is None:
        raise HTTPException(400, "The literature is turned off in the settings; a map needs it to search")
    return lit


def _spawn(key: str, coro: Any) -> None:
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    if key:
        _running[key] = task
        task.add_done_callback(lambda _t: _running.pop(key, None))


def _busy(map_id: str) -> bool:
    t = _running.get(map_id)
    return t is not None and not t.done()


# ---------------------------------------------------------------------------
# Maps


def _map(map_id: str, *, write: bool = False) -> dict[str, Any]:
    m = review_store.map(map_id)
    if m is None:
        raise HTTPException(404, "No such map")
    _space(m["space"])
    mine = m["owner"] == _uid()
    if not mine and not (m["vis"] == "project" and not m["space"].startswith("user:")):
        raise HTTPException(404, "No such map")
    if write and not mine:
        raise HTTPException(403, "Only the map's owner changes it")
    if m["state"] == "building" and not _busy(map_id):        # the server restarted while it was building
        review_store.set_state(map_id, state="failed", error="Interrupted (the server restarted); resume to continue")
        m = review_store.map(map_id)
    return m  # type: ignore[return-value]


def _ideas(space: str, owner: str) -> list[dict[str, Any]]:
    """The space's ideas the map's owner can see."""
    from aurora.brainstorm.store import bs_store
    return [{"card_id": c["card_id"], "text": c["text"]} for c in bs_store.cards(space)
            if c["type"] == "idea" and (c["owner"] == owner or c["vis"] == "project")]


def _build(m: dict[str, Any], size: int = fieldmap.CORE_SIZE) -> fieldmap.Build:
    cid = "" if m["space"].startswith("user:") else m["space"]
    return fieldmap.Build(map_id=m["map_id"], space=m["space"], campaign_id=cid, owner=m["owner"], topic=m["topic"],
                          brief=m["brief"], access=_access(cid), seat_models=_seat_models(), lit=_lit(m["space"], cid),
                          content=m["content"], size=size, ideas=_ideas(m["space"], m["owner"]),
                          on_step=converse.teller(m.get("chat_id", ""), m["map_id"]) if m.get("chat_id") else None,
                          screeners=_screeners(cid), pause=bool(m.get("chat_id")))


def start_map(space: str, topic: str, brief: str = "", *, chat_id: str = "", size: int = fieldmap.CORE_SIZE,
              seeds: list[str] | None = None, question: dict[str, Any] | None = None) -> dict[str, Any]:
    """Make a map of a direction in a space and start building it (its steps are told in ``chat_id``).

    ``question``: the research question the researcher settled with the Mentor; the map's scope is built from it
    and is not asked for again."""
    key, cid = _space(space)
    if cid:
        require(cid, "comment")
    if not topic.strip():
        raise HTTPException(400, "Name the research direction")
    m = review_store.new_map(key, _uid(), topic, brief, chat_id=chat_id)
    if question and question.get("question"):
        m["content"] = {**m["content"], "settled": question, "confirmed": ["scope"]}
        review_store.put_content(m["map_id"], m["content"])
    b = _build(m, max(8, min(40, size)))
    _spawn(m["map_id"], fieldmap.run(b, seeds=[e["paper_id"] for s in seeds or [] if (e := library.paper(s))]))
    return review_store.map(m["map_id"])  # type: ignore[return-value]


def latest_map(space_key: str, viewer: str) -> dict[str, Any] | None:
    """The map of a space a person sees: their latest, else the latest shared with the project."""
    for row in review_store.maps(space_key):
        if row["owner"] == viewer or (row["vis"] == "project" and not space_key.startswith("user:")):
            return review_store.map(row["map_id"])
    return None


def start_amend(map_id: str, step: str, text: str) -> None:
    """The researcher's word at a checkpoint (the scope, or the papers), applied in the background."""
    _start(map_id, lambda b: fieldmap.amend(b, step, text))


def go_on(map_id: str, step: str) -> None:
    _start(map_id, lambda b: fieldmap.go_on(b, step))


def start_challenge(map_id: str, text: str) -> None:
    """The researcher disputes the map in conversation: its consequences are worked out (told in the chat)."""
    _start(map_id, lambda b: _then_ready(map_id, fieldmap.challenge(b, text)))


def start_replace(map_id: str, paper_id: str, note: str) -> None:
    _start(map_id, lambda b: _then_ready(map_id, fieldmap.replace_paper(b, paper_id, note)))


def _paper_view(r: dict[str, Any]) -> dict[str, Any] | None:
    e = library.paper(r["paper_id"])
    if e is None:
        return None
    p = e["paper"]
    return {"paper_id": e["paper_id"], "key": e["key"], "title": p.title, "year": p.year, "venue": p.venue,
            "authors": p.authors[:6], "abstract": p.abstract, "doi": p.doi, "arxiv_id": p.arxiv_id, "url": p.url,
            "role": r["role"], "card": r["card"]}


def _view(m: dict[str, Any]) -> dict[str, Any]:
    content = m["content"]
    papers = [v for r in review_store.papers(m["map_id"]) if (v := _paper_view(r))]
    keys = {p["key"]: p["paper_id"] for p in papers}
    extra = {}
    for rn in content.get("read_next") or []:          # recommended papers that are not in the map
        if rn.get("key") not in keys and (e := library.paper(rn.get("paper_id", ""))):
            extra[e["key"]] = {"paper_id": e["paper_id"], "key": e["key"], "title": e["paper"].title, "year": e["paper"].year}
    return {
        "map_id": m["map_id"], "space": m["space"], "topic": m["topic"], "brief": m["brief"], "vis": m["vis"],
        "state": m["state"], "step": m["step"], "step_name": fieldmap.STEP_NAMES.get(m["step"], ""), "error": m["error"],
        "version": m["version"], "mine": m["owner"] == _uid(), "busy": _busy(m["map_id"]),
        "steps": [{"id": s, "name": fieldmap.STEP_NAMES[s], "done": s in (content.get("done") or [])} for s in fieldmap.STEPS],
        "content": {**content, "distribution": fieldmap.distribution(m["map_id"], content) if content.get("frameworks") else {}},
        "papers": papers, "outside": list(extra.values()),
        "versions": review_store.versions(m["map_id"]), "log": review_store.logs(m["map_id"], 80),
        "new_papers": _new_papers(m),
    }


def _new_papers(m: dict[str, Any]) -> int:
    if m["state"] != "ready":
        return 0
    cid = "" if m["space"].startswith("user:") else m["space"]
    owner = "" if cid else m["owner"]
    have = {r["paper_id"] for r in review_store.papers(m["map_id"])}
    since = m["checked_at"] or m["created_at"]
    return sum(1 for i in library.items(cid, owner=owner) if i["paper_id"] not in have and i["added_at"] > since)


@router.get("/spaces/{space}/maps")
async def list_maps(space: str) -> list[dict[str, Any]]:
    key, _ = _space(space)
    uid = _uid()
    return [{**m, "mine": m["owner"] == uid} for m in review_store.maps(key)
            if m["owner"] == uid or (m["vis"] == "project" and not key.startswith("user:"))]


class NewMap(BaseModel):
    topic: str
    brief: str = ""
    seeds: list[str] = []          # papers (keys or IDs) that must be in it
    size: int = fieldmap.CORE_SIZE


@router.post("/spaces/{space}/maps")
async def new_map(space: str, body: NewMap) -> dict[str, Any]:
    m = start_map(space, body.topic, body.brief, size=body.size, seeds=body.seeds)
    return _view(m)


_placed_at: dict[str, float] = {}


@router.get("/spaces/{space}/pane")
async def map_pane(space: str) -> dict[str, Any] | None:
    """The space's map beside a brainstorm; ideas added since are placed on it in the background."""
    key, _ = _space(space)
    m = latest_map(key, _uid())
    if m is None:
        return None
    m = _map(m["map_id"])
    if m["owner"] == _uid() and m["state"] == "ready" and not _busy(m["map_id"]) and time.time() - _placed_at.get(m["map_id"], 0) > 120:
        unplaced = [i for i in _ideas(m["space"], m["owner"]) if i["card_id"] not in (m["content"].get("ideas") or {})]
        if unplaced:
            _placed_at[m["map_id"]] = time.time()
            try:
                b = _build(m)
                _spawn(m["map_id"], _quietly(_place_and_keep(b, unplaced)))
            except HTTPException:
                pass
    return _view(m)


async def _place_and_keep(b: fieldmap.Build, ideas: list[dict[str, Any]]) -> None:
    await fieldmap.place_ideas(b, ideas)
    review_store.put_content(b.map_id, b.content)


@router.get("/maps/{map_id}")
async def get_map(map_id: str) -> dict[str, Any]:
    m = _map(map_id)
    if m["owner"] == _uid():
        # papers that joined the literature since: placed in the background, at most once an hour
        if m["state"] == "ready" and not _busy(map_id) and time.time() - (m["checked_at"] or 0) > 3600 and _new_papers(m):
            try:
                b = _build(m)
                _spawn(map_id, _quietly(fieldmap.check_new_papers(b)))
            except HTTPException:
                pass
    return _view(_map(map_id))


async def _quietly(coro: Any) -> None:
    try:
        await coro
    except Exception:
        logger.info("a background map task failed", exc_info=True)


class MapChange(BaseModel):
    topic: str | None = None
    shared: bool | None = None


@router.patch("/maps/{map_id}")
async def change_map(map_id: str, body: MapChange) -> dict[str, Any]:
    m = _map(map_id, write=True)
    if body.shared is not None:
        if body.shared and (m["space"].startswith("user:") or "comment" not in rights(current_user(), m["space"])):
            raise HTTPException(403, "A map can be shared only with a project where you may comment")
        review_store.set_state(map_id, vis="project" if body.shared else "private")
    if body.topic is not None and body.topic.strip():
        review_store.set_state(map_id, topic=body.topic.strip()[:300])
    return _view(_map(map_id))


@router.delete("/maps/{map_id}")
async def delete_map(map_id: str) -> dict[str, Any]:
    _map(map_id, write=True)
    if _busy(map_id):
        _running[map_id].cancel()
    review_store.delete_map(map_id)
    return {"deleted": True}


def _start(map_id: str, make: Any) -> dict[str, Any]:
    m = _map(map_id, write=True)
    if _busy(map_id):
        raise HTTPException(409, "The map is being worked on; wait for it to finish")
    b = _build(m)
    _spawn(map_id, _quietly(make(b)))
    review_store.set_state(map_id, state="building")
    return _view(_map(map_id))


class Rerun(BaseModel):
    start: str = ""


@router.post("/maps/{map_id}/run")
async def rerun(map_id: str, body: Rerun) -> dict[str, Any]:
    """Resume a build that stopped (or run again from a step)."""
    m = _map(map_id, write=True)
    start = body.start or fieldmap.resume_from(m["content"])
    if start not in fieldmap.STEPS:
        raise HTTPException(400, f"start is one of {list(fieldmap.STEPS)}")
    reason = "Built" if not m["version"] else f"Rebuilt from “{fieldmap.STEP_NAMES[start]}”"
    return _start(map_id, lambda b: fieldmap.run(b, start=start, reason=reason))


@router.post("/maps/{map_id}/proposals/{proposal_id}/accept")
async def accept(map_id: str, proposal_id: str) -> dict[str, Any]:
    return _start(map_id, lambda b: fieldmap.accept_proposal(b, proposal_id))


@router.post("/maps/{map_id}/proposals/{proposal_id}/dismiss")
async def dismiss(map_id: str, proposal_id: str) -> dict[str, Any]:
    m = _map(map_id, write=True)
    c = m["content"]
    for p in c.get("proposals") or []:
        if p["id"] == proposal_id:
            p["state"] = "dismissed"
    review_store.put_content(map_id, c)
    return _view(_map(map_id))


class Choose(BaseModel):
    framework: str


@router.post("/maps/{map_id}/framework")
async def choose(map_id: str, body: Choose) -> dict[str, Any]:
    return _start(map_id, lambda b: fieldmap.choose_framework(b, body.framework))


class Replace(BaseModel):
    note: str


@router.post("/maps/{map_id}/papers/{paper_id}/replace")
async def replace(map_id: str, paper_id: str, body: Replace) -> dict[str, Any]:
    if not body.note.strip():
        raise HTTPException(400, "Say what is wrong with the placement")
    return _start(map_id, lambda b: _then_ready(map_id, fieldmap.replace_paper(b, paper_id, body.note.strip())))


async def _then_ready(map_id: str, coro: Any) -> None:
    try:
        await coro
    finally:
        review_store.set_state(map_id, state="ready", step="")


class Challenge(BaseModel):
    text: str


@router.post("/maps/{map_id}/challenges")
async def challenge(map_id: str, body: Challenge) -> dict[str, Any]:
    """Dispute the definition: its consequences come back as options (nothing changes until one is applied)."""
    if not body.text.strip():
        raise HTTPException(400, "Say what you dispute")
    return _start(map_id, lambda b: _then_ready(map_id, fieldmap.challenge(b, body.text.strip())))


class Apply(BaseModel):
    option: str


@router.post("/maps/{map_id}/challenges/{challenge_id}/apply")
async def apply(map_id: str, challenge_id: str, body: Apply) -> dict[str, Any]:
    return _start(map_id, lambda b: fieldmap.apply_challenge(b, challenge_id, body.option))


@router.post("/maps/{map_id}/check-new")
async def check_new(map_id: str) -> dict[str, Any]:
    return _start(map_id, lambda b: _then_ready(map_id, fieldmap.check_new_papers(b)))


@router.get("/maps/{map_id}/versions/{version}")
async def get_version(map_id: str, version: int) -> dict[str, Any]:
    _map(map_id)
    v = review_store.version(map_id, version)
    if v is None:
        raise HTTPException(404, "No such version")
    return v


def _to_brainstorm(space: str, owner: str, text: str, evidence: list[tuple[str, str, str]], vis: str = "private") -> dict[str, Any]:
    """An idea card in the space, with papers as material linked to it."""
    from aurora.brainstorm.store import bs_store
    idea = bs_store.add_card(space, owner, "idea", text, by=owner, vis=vis)
    for pid, note, rel in evidence:
        e = library.paper(pid)
        if e is None:
            continue
        card = bs_store.add_card(space, owner, "material", f"{e['paper'].title}. {note}".strip(), by=owner, vis=vis,
                                 paper_id=pid)
        bs_store.link(card["card_id"], idea["card_id"], rel)
    return idea


@router.post("/maps/{map_id}/problems/{problem_id}/brainstorm")
async def problem_to_brainstorm(map_id: str, problem_id: str) -> dict[str, Any]:
    m = _map(map_id)
    p = next((x for x in m["content"].get("problems") or [] if x.get("id") == problem_id), None)
    if p is None:
        raise HTTPException(404, "No such problem")
    keys = {library.paper(k)["paper_id"]: k for k in p.get("evidence") or [] if library.paper(k)}
    evidence = [(pid, "Evidence that the problem is not well solved.", "related") for pid in keys]
    evidence += [(e["paper_id"], c.get("difference", ""), "related") for c in p.get("closest") or []
                 if (e := library.paper(c.get("key", "")))]
    text = f"{p['title']}. {p.get('gap', '')}".strip()
    idea = _to_brainstorm(m["space"], _uid(), text[:1200], evidence)
    for x in m["content"].get("problems") or []:
        if x.get("id") == problem_id:
            x["card_id"] = idea["card_id"]
    if m["owner"] == _uid():
        review_store.put_content(map_id, m["content"])
    return {"card_id": idea["card_id"], "links": len(evidence)}


# ---------------------------------------------------------------------------
# Deep reads


def _read(read_id: str) -> dict[str, Any]:
    r = review_store.read_by_id(read_id)
    if r is None or r["owner"] != _uid():
        raise HTTPException(404, "No such deep read")
    return r


def _read_view(r: dict[str, Any]) -> dict[str, Any]:
    e = library.paper(r["paper_id"])
    answers = r["answers"]
    out = {**r, "paper": {"paper_id": r["paper_id"], "key": e["key"] if e else "", "title": e["paper"].title if e else ""},
           "questions": [{"n": q["n"], "title": q["title"], "key": bool(q.get("key")), "agent": q.get("agent", "")}
                         for q in deepread.QUESTIONS],
           "busy": _busy(r["read_id"])}
    # an AI answer shows once the reader has answered the question themselves, or asked to see it
    visible = {int(k) for k in r["mine"]} | set(r["shown"])
    out["answers"] = {k: (a if int(k) in visible or a.get("state") != "done" else {"state": "hidden", "ready": True})
                      for k, a in answers.items()}
    out["answered"] = sorted(int(k) for k in r["mine"])
    out["ready"] = sorted(int(k) for k, a in answers.items() if a.get("state") == "done")
    return out


class OpenRead(BaseModel):
    paper: str
    space: str = ""
    map_id: str = ""
    start: bool = True


async def _text_of(paper_id: str, owner: str, campaign_id: str) -> tuple[str, list[str]]:
    from aurora.literature import reading
    path = reading.find_pdf(paper_id, owner, campaign_id) or await reading.fetch_open_pdf(paper_id)
    if path is None:
        raise HTTPException(400, "There is no PDF of this paper to read; put one in the project's papers or your Zotero")
    pages = reading.page_texts(path)
    if not any(p.strip() for p in pages):
        raise HTTPException(400, "The PDF has no text layer to read")
    return "\n\n".join(f"[page {i + 1}]\n{p}" for i, p in enumerate(pages)), pages


async def start_read(paper: str, space: str, *, map_id: str = "", chat_id: str = "", start: bool = True) -> dict[str, Any]:
    """Open a deep read (and answer its questions in the background); from a conversation, it ends there with a
    question to the researcher: how would they have come to the idea?"""
    e = library.paper(paper)
    if e is None:
        raise HTTPException(404, "No such paper")
    key, cid = _space(space)
    if map_id:
        map_id = _map(map_id)["map_id"]
    elif m := latest_map(key, _uid()):
        map_id = m["map_id"]
    r = review_store.open_read(_uid(), e["paper_id"], key, map_id, chat_id=chat_id)
    if start and not r["answers"] and not _busy(r["read_id"]):
        access = _access(cid)
        text, pages = await _text_of(e["paper_id"], _uid(), cid)
        lit = _lit(key, cid)

        async def go() -> None:
            await deepread.read_all(access, r["read_id"], e, text, pages, lit=lit)
            if chat_id:
                _ask_for_guess(r["read_id"], chat_id, e["key"])

        _spawn(r["read_id"], _quietly(go()))
        for q in deepread.QUESTIONS:
            review_store.set_answer(r["read_id"], q["n"], {"state": "pending"})
    return review_store.read_by_id(r["read_id"])  # type: ignore[return-value]


def _ask_for_guess(read_id: str, chat_id: str, key: str) -> None:
    from aurora.brainstorm.store import bs_store
    if bs_store.chat(chat_id) is None:
        return
    bs_store.add_message(chat_id, "Librarian", f"I have read [{key}] closely. Before I show how its authors may have "
                         "come to the idea: how would you have, knowing only what was known before it? Reply here in a "
                         "few sentences, or skip.", kind="action", widget={"type": "guess", "read_id": read_id, "state": "waiting"})


def reveal(read_id: str, chat_id: str, guess: str = "") -> None:
    """The researcher guessed (or skipped): the authors' path, told in the conversation."""
    from aurora.brainstorm.store import bs_store
    r = review_store.read_by_id(read_id)
    if r is None:
        return
    mine, shown = dict(r["mine"]), sorted(set(r["shown"]) | {3})
    fields: dict[str, Any] = {"revealed": True, "shown": shown}
    if guess:
        fields["guess"] = guess
        mine["3"] = {"text": guess, "at": time.time()}
        fields["mine"] = mine
    review_store.update_read(read_id, **fields)
    r = review_store.read_by_id(read_id)
    if r and chat_id:
        bs_store.add_message(chat_id, "Librarian", converse.path_text(r), kind="action",
                             widget={"type": "path", "read_id": read_id, "paper_id": r["paper_id"]})


@router.post("/reads")
async def open_read(body: OpenRead) -> dict[str, Any]:
    r = await start_read(body.paper, body.space, map_id=body.map_id, start=body.start)
    return _read_view(r)


@router.get("/reads")
async def find_read(paper: str, space: str = "") -> dict[str, Any] | None:
    e = library.paper(paper)
    if e is None:
        raise HTTPException(404, "No such paper")
    key, _ = _space(space)
    r = review_store.read(_uid(), e["paper_id"], key)
    return _read_view(r) if r else None


@router.get("/reads/{read_id}")
async def get_read(read_id: str) -> dict[str, Any]:
    return _read_view(_read(read_id))


class ReadChange(BaseModel):
    guess: str | None = None          # question 3's answer (as the conversation and older consoles send it)
    revealed: bool | None = None      # question 3's AI answer shown
    ticks: list[int] | None = None
    question: int | None = None       # with answer and/or show: the reader's own answer to a question, its AI answer shown
    answer: str | None = None
    show: bool | None = None


@router.patch("/reads/{read_id}")
async def change_read(read_id: str, body: ReadChange) -> dict[str, Any]:
    r = _read(read_id)
    fields: dict[str, Any] = {k: v for k, v in body.model_dump().items() if k in ("guess", "revealed", "ticks") and v is not None}
    mine, shown = dict(r["mine"]), list(r["shown"])
    q = body.question or (3 if body.guess is not None or body.revealed is not None else None)
    if q is not None and not 1 <= q <= 12:
        raise HTTPException(400, "Questions are numbered 1 to 12")
    text = body.answer if body.answer is not None else body.guess
    if q is not None and text is not None:
        if text.strip():
            mine[str(q)] = {"text": text.strip()[:6000], "at": time.time()}
        else:
            mine.pop(str(q), None)
    if q is not None and (body.show or body.revealed) and q not in shown:
        shown.append(q)
    fields.update(mine=mine, shown=shown)
    if q == 3 and text is not None:
        fields["guess"] = text.strip()
    if q == 3 and (body.show or body.revealed):
        fields["revealed"] = True
    review_store.update_read(read_id, **fields)
    return _read_view(_read(read_id))


class Again(BaseModel):
    questions: list[int]


@router.post("/reads/{read_id}/again")
async def again(read_id: str, body: Again) -> dict[str, Any]:
    r = _read(read_id)
    if _busy(read_id):
        raise HTTPException(409, "Questions are still being answered")
    qs = sorted({n for n in body.questions if 1 <= n <= 12})
    if not qs:
        raise HTTPException(400, "Name the questions (1 to 12)")
    cid = "" if r["space"].startswith("user:") else r["space"]
    access = _access(cid)
    e = library.paper(r["paper_id"])
    text, pages = await _text_of(r["paper_id"], _uid(), cid)
    _spawn(read_id, _quietly(deepread.read_all(access, read_id, e, text, pages, only=qs, lit=_lit(r["space"], cid))))
    for n in qs:
        review_store.set_answer(read_id, n, {"state": "pending"})
    return _read_view(_read(read_id))


class FollowUp(BaseModel):
    question: int
    text: str


@router.post("/reads/{read_id}/followups")
async def follow_up(read_id: str, body: FollowUp) -> dict[str, Any]:
    """Ask more about one question; the answer comes from the PDF, in the same labelled style."""
    r = _read(read_id)
    if not 1 <= body.question <= 12 or not body.text.strip():
        raise HTTPException(400, "Name the question and say what you want to know")
    cid = "" if r["space"].startswith("user:") else r["space"]
    access = _access(cid)
    e = library.paper(r["paper_id"])
    text, pages = await _text_of(r["paper_id"], _uid(), cid)
    prev = r["answers"].get(str(body.question)) or {}
    so_far = "\n".join(p.get("text", "") for p in prev.get("paragraphs") or [])
    if str(body.question) not in r["mine"] and body.question not in r["shown"]:
        raise HTTPException(400, "Answer the question yourself (or show the AI's answer) before asking more")
    out = await rllm.ask(access, "deep_read_followup", f"""{deepread._context(e, text, '')}

Question {body.question}: {deepread.TITLES[body.question]}
The answer so far:
{so_far}

The reader asks: {body.text.strip()}

Answer from the paper. Where a paragraph rests on the paper, give in "quote" a few words copied exactly from it.
JSON: {{"paragraphs": {deepread.QPARAS}}}""", want="paragraphs")
    fu = {"q": body.text.strip(), "paragraphs": deepread._paras(out.get("paragraphs"), pages)}
    answer = {**prev, "followups": [*(prev.get("followups") or []), fu]}
    review_store.set_answer(read_id, body.question, answer)
    return _read_view(_read(read_id))


@router.post("/reads/{read_id}/idea")
async def idea_to_brainstorm(read_id: str) -> dict[str, Any]:
    r = _read(read_id)
    a = r["answers"].get("12") or {}
    if a.get("state") != "done" or not a.get("idea"):
        raise HTTPException(400, "The follow-up idea is not ready")
    evidence = [(r["paper_id"], f"The idea starts from it: {a.get('source', '')}", "related")]
    evidence += [(c["paper_id"], c.get("difference", ""), "related") for c in a.get("closest") or [] if c.get("paper_id")]
    idea = _to_brainstorm(r["space"], _uid(), a["idea"][:1200], evidence)
    review_store.set_answer(read_id, 12, {**a, "card_id": idea["card_id"]})
    return {"card_id": idea["card_id"], "links": len(evidence)}
