"""Pippin at your side: one conversation, from the desktop orb (typed or spoken) or the browser's side panel, about
everything the console knows, without opening it.

It is the front desk's tool loop with the whole system in reach rather than one project: your memory (what you
saved and read, what is in front of you, the digest; through the voice assistant, so "this paragraph", "this
figure", "the second one" mean what they mean when spoken), your projects and their runs (status, decisions waiting,
what the front desk last said), and links that open the right page of the console when something needs the big
screen.  In this first version it changes nothing in a project: starting, steering or deciding is done on the
project's page, which it opens.  Turns are kept per person, so a conversation goes on where it stopped.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Any

from aurora.human.frontdesk import FrontDeskAgent, FrontDeskTool

logger = logging.getLogger(__name__)

TURNS = 200                      # kept per person
SPOKEN = 260                     # a reply longer than this is shown on the panel too, not only said

SYSTEM = """You are Pippin, the research assistant at the researcher's side: a small orb on their desktop, or a \
panel beside what they read. They ask you things in passing, without opening the console; answer so they need \
not open it.

You can:
- answer from their memory and about what is in front of them (a page, a paper, a selection, a figure), keep \
something they want kept, note a thought, read them today's digest: use `memory` with what they asked, in their \
words, for anything about what they read, saved or are looking at;
- see what is in front of them: `in_front`;
- tell them about their research projects and the runs in them (what is running, how far, what it costs, what \
waits for their decision, what the project's front desk last said): `projects`, then `project`;
- give them a button that opens the right page of the console: `open_in_console` (it opens nothing by itself: \
say the button is there, never that you opened the page).

You do not change projects yet: starting, steering or stopping a run, or answering a decision it waits for, is \
done on the project's page. When they ask for that, say so in a sentence and give them the button.

Answer in their language, briefly: one to three sentences, plain text, no headings or lists unless they ask for \
a list; they may be listening rather than reading. Name the project or paper you mean. Never make up a status, \
a number or a source: look it up, and say when you could not."""


def _home() -> Path:
    from aurora.paths import aurora_home
    d = aurora_home() / "assistant"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _file(owner: str) -> Path:
    return _home() / f"{re.sub(r'[^A-Za-z0-9_.-]', '_', owner)}.json"


def history(owner: str, limit: int = 60) -> list[dict[str, Any]]:
    try:
        turns = json.loads(_file(owner).read_text())
    except (OSError, ValueError):
        return []
    return turns[-limit:]


def _save(owner: str, turns: list[dict[str, Any]]) -> None:
    _file(owner).write_text(json.dumps(turns[-TURNS:], ensure_ascii=False))


def clear(owner: str) -> None:
    """A new conversation (the old one is forgotten)."""
    _file(owner).unlink(missing_ok=True)
    _agents.pop(owner, None)


class _Turn:
    """What the tools of one turn gathered for the reply: sources from the memory, buttons, what can be undone."""

    def __init__(self, screenshot: str = "") -> None:
        self.screenshot = screenshot
        self.items: list[dict[str, Any]] = []
        self.links: list[dict[str, str]] = []
        self.undo_id = ""
        self.memory_text = ""


_agents: dict[str, tuple[FrontDeskAgent, dict[str, _Turn]]] = {}
_locks: dict[str, asyncio.Lock] = {}


def _tools(owner: str, now: dict[str, _Turn], mem_access: Any, models: dict[str, str]) -> list[FrontDeskTool]:
    from aurora.memory import voice

    async def memory(p: dict[str, Any]) -> Any:
        t = now["turn"]
        said = str(p.get("request") or "").strip()
        out = await voice.hear(mem_access, models, owner, said, screenshot=t.screenshot)
        t.items.extend(out.get("items") or [])
        t.undo_id = out.get("undo_id") or t.undo_id
        t.memory_text = out.get("text") or ""
        return {"did": out.get("intent"), "answer": out.get("text") or out.get("speech"), "spoken": out.get("speech"),
                "sources": [i.get("claim") or i.get("text") or "" for i in out.get("items") or []][:8],
                "can_undo": bool(out.get("undo_id"))}

    async def in_front(p: dict[str, Any]) -> Any:
        return voice._scene_text(voice.scene(owner))

    async def projects(p: dict[str, Any]) -> Any:
        from aurora.api.routes.campaigns import list_campaigns
        return [{"project_id": c["campaign_id"], "name": c["name"], "runs": c.get("experiment_count", 0),
                 "running": c.get("active_experiments", 0), "needs_attention": c.get("needs_attention", False),
                 "updated": time.strftime("%Y-%m-%d %H:%M", time.localtime(c.get("updated_at") or 0))}
                for c in await list_campaigns()]

    async def project(p: dict[str, Any]) -> Any:
        from aurora.api.routes.campaigns import exp_chat_history, get_campaign, list_campaigns
        want = str(p.get("project") or "").strip().lower()
        rows = await list_campaigns()
        hit = next((c for c in rows if c["campaign_id"].lower() == want), None) or \
            next((c for c in rows if want and want in c["name"].lower()), None)
        if hit is None:
            return {"error": f"No project matches “{p.get('project')}”", "projects": [c["name"] for c in rows]}
        c = await get_campaign(hit["campaign_id"])
        runs = []
        for e in c.get("experiments", [])[:5]:
            runs.append({"run_id": e["run_id"], "task": e.get("task_name"), "status": e.get("status"),
                         "phase": e.get("phase"), "cost_usd": e.get("cost_usd"), "error": e.get("error"),
                         "waiting_for_you": (e.get("pending_decision") or {}).get("question")
                         or (e.get("pending_decision") or {}).get("title"),
                         "started": time.strftime("%Y-%m-%d %H:%M", time.localtime(e.get("started_at") or 0))})
        said = []
        if runs:
            try:
                said = [{"who": m.get("role"), "text": str(m.get("text") or m.get("content") or "")[:400]}
                        for m in (await exp_chat_history(c["campaign_id"], runs[0]["run_id"], limit=6))]
            except Exception:
                logger.debug("no chat for %s", runs[0]["run_id"], exc_info=True)
        return {"project_id": c["campaign_id"], "name": c["name"], "objective": c.get("objective", "")[:600],
                "runs": runs, "latest_run_conversation": said}

    async def open_in_console(p: dict[str, Any]) -> Any:
        page = str(p.get("page") or "home")
        pid, rid = str(p.get("project_id") or ""), str(p.get("run_id") or "")
        if page == "run" and pid and rid:
            path, label = f"/campaigns/{pid}/experiments/{rid}", "Open the run"
        elif page in ("project", "run") and pid:
            path, label = f"/campaigns/{pid}", "Open the project"
        elif page in ("library", "memory", "brainstorm"):
            path, label = f"/?page={page}", f"Open the {page}"
        else:
            path, label = "/", "Open the console"
        label = str(p.get("label") or label)[:60]
        now["turn"].links.append({"label": label, "path": path})
        return {"button": label, "opens": path}

    obj = {"type": "object"}
    return [
        FrontDeskTool("memory", "Their memory and what is in front of them: answer a question from it, compare, keep "
                      "something, note a thought, read the digest, undo. Give their request in their words.",
                      {**obj, "properties": {"request": {"type": "string"}}, "required": ["request"]}, memory),
        FrontDeskTool("in_front", "What is open in front of them now, what they had open before, what they saved lately.",
                      {**obj, "properties": {}}, in_front),
        FrontDeskTool("projects", "Their research projects: name, runs, how many are running, whether one needs them.",
                      {**obj, "properties": {}}, projects),
        FrontDeskTool("project", "One project (by name or ID): its runs with status, phase, cost, errors, the decision "
                      "waiting for them, and the latest run's last messages with its front desk.",
                      {**obj, "properties": {"project": {"type": "string"}}, "required": ["project"]}, project),
        FrontDeskTool("open_in_console", "A button that opens a page of the console: home, project, run (with "
                      "project_id and run_id), library, memory or brainstorm.",
                      {**obj, "properties": {"page": {"type": "string", "enum": [
                          "home", "project", "run", "library", "memory", "brainstorm"]},
                          "project_id": {"type": "string"}, "run_id": {"type": "string"},
                          "label": {"type": "string"}}, "required": ["page"]}, open_in_console),
    ]


def _agent(owner: str, access: Any, mem_access: Any, models: dict[str, str]) -> tuple[FrontDeskAgent, dict[str, _Turn]]:
    have = _agents.get(owner)
    if have is not None:
        return have
    now: dict[str, _Turn] = {"turn": _Turn()}

    def status() -> str:
        from aurora.memory import surface
        cur = surface.current_context(owner) or {}
        front = cur.get("title") or cur.get("url") or "nothing known"
        return f"Now: {time.strftime('%A %Y-%m-%d %H:%M')}. In front of them: {front}."
    agent = FrontDeskAgent(model=access.model, api_key=access.api_key, base_url=access.base_url,
                           system_prompt=SYSTEM, tools=_tools(owner, now, mem_access, models), status_fn=status)
    agent.restore([{"role": t["role"], "text": t["text"]} for t in history(owner, 40)])
    _agents[owner] = (agent, now)
    return agent, now


async def ask(owner: str, text: str, *, access: Any, mem_access: Any, models: dict[str, str], screenshot: str = "",
              via: str = "typed") -> dict[str, Any]:
    """One turn: what they said, answered with the tools. Returns the reply as the orb and the panel show it."""
    text = text.strip()
    lock = _locks.setdefault(owner, asyncio.Lock())
    async with lock:
        agent, now = _agent(owner, access, mem_access, models)
        now["turn"] = turn = _Turn(screenshot)
        out = await agent.handle_user_message(text)
        reply = out.get("text") or ""
        at = time.time()
        mine = {"turn_id": uuid.uuid4().hex[:10], "role": "user", "text": text, "at": at, "via": via}
        theirs = {"turn_id": uuid.uuid4().hex[:10], "role": "assistant", "text": reply, "at": time.time(),
                  "items": turn.items[:8], "links": turn.links[:4], "undo_id": turn.undo_id}
        _save(owner, [*history(owner, TURNS), mine, theirs])
    # for the orb: what to say aloud, and whether the panel should open with the rest
    speech = reply if len(reply) <= SPOKEN else re.split(r"(?<=[.!?。！？])\s*", reply, maxsplit=1)[0]
    full = len(reply) > SPOKEN or bool(turn.items) or bool(turn.links)
    return {**theirs, "said": text, "reply": reply, "speech": speech, "text": reply if full else ""}
