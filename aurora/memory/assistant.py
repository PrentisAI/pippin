"""Pippin at your side: one conversation, from the desktop orb (typed or spoken) or the browser's side panel, about
everything the console knows, without opening it.

It is the front desk's tool loop with the whole system in reach rather than one project: your memory (what you
saved and read, what is in front of you, the digest; through the voice assistant, so "this paragraph", "this
figure", "the second one" mean what they mean when spoken), your projects and their runs (status, decisions waiting,
what the front desk last said), and links that open the right page of the console when something needs the big
screen.  It reads a run's report, and it may propose to act on a run (answer the decision it waits for, tell it or
its project something, stop it): a proposal is done only when the person confirms it, with its button or by
replying "确认" / "yes" in a chat app.  Turns are kept per person, so a conversation goes on where it stopped.
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
- read what a run found and wrote: `report` (its report, claims, results, the direction it took);
- act on a run when they ask you to: answer the decision it waits for, tell the run or the project something, or \
stop it: `propose`. It does nothing yet: they confirm it (a button, or replying 确认 / yes), and only then is it \
done. Say in one sentence what you proposed and that it waits for their confirmation; never say it is done;
- give them a button that opens the right page of the console: `open_in_console` (it opens nothing by itself: \
say the button is there, never that you opened the page).

Starting a new run, changing its settings or sharing a project is done on the project's page: say so and give \
them the button. Propose an action only when they asked for it, never on your own.

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
        self.actions: list[dict[str, Any]] = []


_agents: dict[str, tuple[FrontDeskAgent, dict[str, _Turn]]] = {}
_locks: dict[str, asyncio.Lock] = {}


REPORT = 14000                   # characters of a report given to the model
LIVE = ("running", "starting", "awaiting_human", "paused")
ACTION_TTL = 24 * 3600           # a proposal not confirmed within a day is no longer done


def _clip(text: str, n: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


async def _project(want: Any) -> dict[str, Any]:
    """The project they mean (its ID, or a part of its name), with its runs; or why there is none."""
    from aurora.api.routes.campaigns import get_campaign, list_campaigns
    w = str(want or "").strip().lower()
    rows = await list_campaigns()
    hit = next((c for c in rows if c["campaign_id"].lower() == w), None) or \
        next((c for c in rows if w and w in c["name"].lower()), None)
    if hit is None:
        return {"error": f"No project matches “{want}”", "projects": [c["name"] for c in rows]}
    return await get_campaign(hit["campaign_id"])


def _run_of(c: dict[str, Any], want: Any, prefer: Any) -> dict[str, Any] | None:
    """The run they mean in a project (its ID, or a part of its name); without one, the latest that *prefer*s."""
    runs = sorted(c.get("experiments", []), key=lambda e: e.get("started_at") or 0, reverse=True)
    w = str(want or "").strip().lower()
    if w:
        return next((e for e in runs if e["run_id"].lower() == w), None) or \
            next((e for e in runs if w in (e.get("task_name") or "").lower()), None)
    return next((e for e in runs if prefer(e)), None) or (runs[0] if runs else None)


def _report_path(campaign_id: str, run_id: str) -> Path | None:
    from aurora.api.routes.run_results import _run
    drafts = sorted((_run(campaign_id, run_id).campaign_dir / "phases" / "writing").glob("draft_*.md"),
                    key=lambda p: int(re.sub(r"\D", "", p.stem) or 0))
    return drafts[-1] if drafts else None


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
        from aurora.api.routes.campaigns import exp_chat_history
        c = await _project(p.get("project"))
        if "error" in c:
            return c
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

    async def report(p: dict[str, Any]) -> Any:
        from aurora.api.routes.run_results import _run, overview
        c = await _project(p.get("project"))
        if "error" in c:
            return c
        e = _run_of(c, p.get("run"), lambda e: bool(_report_path(c["campaign_id"], e["run_id"])))
        if e is None:
            return {"error": "No such run in this project", "runs": [x.get("task_name") or x["run_id"] for x in c.get("experiments", [])]}
        run = _run(c["campaign_id"], e["run_id"])
        o = overview(run)
        out: dict[str, Any] = {"project": c["name"], "run_id": e["run_id"], "run": o["name"], "status": o["status"],
                               "cost_usd": o["cost_usd"], "direction": o["proposal"], "claims": o["claims"][:20],
                               "results": [{k: v for k, v in r.items() if k in ("name", "value", "unit", "uncertainty", "description")}
                                           for r in o["results"][:20]]}
        path = _report_path(c["campaign_id"], e["run_id"])
        if path is None:
            out["report"] = "This run has written no report (yet)."
            return out
        md = path.read_text(errors="replace")
        if len(md) > REPORT:
            heads = [ln for ln in md.splitlines() if ln.startswith("#")][:40]
            out["report_outline"] = heads
            md = md[:REPORT] + f"\n[… the report goes on for {len(md) - REPORT} more characters]"
        out["report"] = md
        return out

    async def propose(p: dict[str, Any]) -> Any:
        c = await _project(p.get("project"))
        if "error" in c:
            return c
        what = str(p.get("action") or "")
        text = str(p.get("text") or "").strip()[:4000]
        if what == "message_project":
            if not text:
                return {"error": "Give the message"}
            a = {"kind": "message", "project_id": c["campaign_id"], "project": c["name"], "run_id": "", "run": "",
                 "text": text, "label": f"Send to {c['name']}’s front desk: “{_clip(text, 140)}”"}
        else:
            waiting = (lambda e: bool(e.get("pending_decision"))) if what == "decide" else (lambda e: e.get("status") in LIVE)
            e = _run_of(c, p.get("run"), waiting)
            if e is None:
                return {"error": "No such run in this project", "runs": [x.get("task_name") or x["run_id"] for x in c.get("experiments", [])]}
            name = e.get("task_name") or e["run_id"]
            base = {"project_id": c["campaign_id"], "project": c["name"], "run_id": e["run_id"], "run": name}
            if what == "decide":
                d = e.get("pending_decision")
                choice = str(p.get("choice") or "").lower()
                if not d:
                    return {"error": f"{name} waits for no decision"}
                if choice not in (d.get("options") or ["continue", "stop"]) or choice == "propose":
                    return {"error": f"Answer with one of {[o for o in d.get('options') or [] if o != 'propose']}"}
                if choice == "instruct" and not text:
                    return {"error": "An instruction needs its text"}
                title = (d.get("detail") or {}).get("title") or d.get("context", "").split("\n")[0]
                verb = {"continue": "Let it continue", "stop": "Stop it", "instruct": f"Reply “{_clip(text, 140)}”"}[choice]
                a = {**base, "kind": "decide", "choice": choice, "text": text, "decision_id": d.get("decision_id", ""),
                     "label": f"{verb} — {name}, waiting on “{_clip(title, 100)}”"}
            elif what == "message_run":
                if not text:
                    return {"error": "Give the message"}
                a = {**base, "kind": "message", "text": text, "label": f"Tell {name}: “{_clip(text, 140)}”"}
            elif what == "stop":
                if e.get("status") not in LIVE:
                    return {"error": f"{name} is not running ({e.get('status')})"}
                a = {**base, "kind": "stop", "text": "", "label": f"Stop {name} (it saves its work and ends)"}
            else:
                return {"error": "action is decide, message_run, message_project or stop"}
        a = {**a, "action_id": uuid.uuid4().hex[:12], "state": "pending", "result": "", "at": time.time()}
        now["turn"].actions.append(a)
        return {"proposed": a["label"], "state": "waiting for their confirmation (a button, or replying 确认 / yes)"}

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
        FrontDeskTool("report", "What a run found and wrote: its report (Markdown), claims with their status, recorded "
                      "results, the direction it took. Give the project, and the run (name or ID; default: the latest "
                      "with a report).",
                      {**obj, "properties": {"project": {"type": "string"}, "run": {"type": "string"}},
                       "required": ["project"]}, report),
        FrontDeskTool("propose", "Propose to act on a project, done only when they confirm: decide (answer the decision "
                      "a run waits for; choice continue, stop or instruct with text), message_run (tell a run's agent "
                      "something, in text), message_project (ask or tell the project's front desk, in text), stop (stop "
                      "a running run). Only when they asked for it.",
                      {**obj, "properties": {"action": {"type": "string", "enum": ["decide", "message_run", "message_project", "stop"]},
                                             "project": {"type": "string"}, "run": {"type": "string"},
                                             "choice": {"type": "string", "enum": ["continue", "stop", "instruct"]},
                                             "text": {"type": "string"}},
                       "required": ["action", "project"]}, propose),
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
    # what came of the proposals is part of what was said
    agent.restore([{"role": t["role"], "text": t["text"] + "".join(
        f"\n[Proposed: {a['label']} · {a['state']}{': ' + a['result'] if a.get('result') else ''}]" for a in t.get("actions") or [])}
        for t in history(owner, 40)])
    _agents[owner] = (agent, now)
    return agent, now


async def ask(owner: str, text: str, *, access: Any, mem_access: Any, models: dict[str, str], screenshot: str = "",
              via: str = "typed", context: str = "") -> dict[str, Any]:
    """One turn: what they said, answered with the tools. Returns the reply as the orb and the panel show it.
    *context* is what they are looking at in the console when they asked (given to the model, not kept as their words)."""
    text = text.strip()
    lock = _locks.setdefault(owner, asyncio.Lock())
    async with lock:
        agent, now = _agent(owner, access, mem_access, models)
        now["turn"] = turn = _Turn(screenshot)
        out = await agent.handle_user_message(f"[They are looking at: {context.strip()}]\n{text}" if context.strip() else text)
        reply = out.get("text") or ""
        at = time.time()
        mine = {"turn_id": uuid.uuid4().hex[:10], "role": "user", "text": text, "at": at, "via": via}
        theirs = {"turn_id": uuid.uuid4().hex[:10], "role": "assistant", "text": reply, "at": time.time(),
                  "items": turn.items[:8], "links": turn.links[:4], "undo_id": turn.undo_id, "actions": turn.actions[:3]}
        _save(owner, [*history(owner, TURNS), mine, theirs])
    # for the orb: what to say aloud, and whether the panel should open with the rest
    speech = reply if len(reply) <= SPOKEN else re.split(r"(?<=[.!?。！？])\s*", reply, maxsplit=1)[0]
    full = len(reply) > SPOKEN or bool(turn.items) or bool(turn.links) or bool(turn.actions)
    return {**theirs, "said": text, "reply": reply, "speech": speech, "text": reply if full else ""}


def pending(owner: str) -> list[dict[str, Any]]:
    """Proposals still waiting for their confirmation, the latest first."""
    out = [a for t in history(owner, TURNS) for a in t.get("actions") or [] if a.get("state") == "pending"
           and time.time() - a.get("at", 0) < ACTION_TTL]
    return out[::-1]


async def act(owner: str, action_id: str, confirm: bool) -> dict[str, Any]:
    """Do a proposal they confirmed (as them: their rights apply), or drop it. Returns it, with what came of it."""
    from aurora.api.routes import campaigns as cp
    from aurora.auth.guard import require
    turns = history(owner, TURNS)
    a = next((x for t in turns for x in t.get("actions") or [] if x.get("action_id") == action_id), None)
    if a is None:
        raise KeyError(action_id)
    if a["state"] != "pending":
        return a
    if not confirm:
        a.update(state="declined", result="Not done.")
    elif time.time() - a.get("at", 0) >= ACTION_TTL:
        a.update(state="expired", result="Too old to do now: ask again.")
    else:
        cid, rid = a["project_id"], a.get("run_id") or ""
        try:
            if a["kind"] == "decide":
                require(cid, "decide")
                await cp.submit_decision(cid, rid, cp.DecisionRequest(action=a["choice"], message=a.get("text", "")))
                done = {"continue": "It continues.", "stop": "It stops after saving its work.",
                        "instruct": "It has your instruction."}[a["choice"]]
            elif a["kind"] == "stop":
                require(cid, "run")
                await cp.stop_experiment(cid, rid)
                done = "It stops at the next safe point and keeps its files."
            elif rid:
                require(cid, "run")
                r = await cp.exp_chat_send(cid, rid, cp.ChatBody(text=a["text"]))
                done = _clip(str(r.get("response") or "Sent."), 600)
            else:
                require(cid, "view")
                r = await cp.campaign_chat_send(cid, cp.ChatBody(text=a["text"]))
                done = _clip(str(r.get("response") or r.get("error") or "Sent."), 600)
            a.update(state="done", result=done)
        except Exception as exc:
            detail = getattr(exc, "detail", None) or str(exc)
            a.update(state="failed", result=f"It did not work: {detail}")
    async with _locks.setdefault(owner, asyncio.Lock()):
        fresh = history(owner, TURNS)
        for t in fresh:
            for i, x in enumerate(t.get("actions") or []):
                if x.get("action_id") == action_id:
                    t["actions"][i] = a
        _save(owner, fresh)
        _agents.pop(owner, None)     # the next turn knows what came of it
    return a


def note(owner: str, text: str) -> None:
    """Something Pippin told them outside the conversation (a notification in a chat app), so a reply to it is
    understood."""
    turns = history(owner, TURNS)
    turns.append({"turn_id": uuid.uuid4().hex[:10], "role": "assistant", "text": text, "at": time.time(), "via": "notice"})
    _save(owner, turns)
    _agents.pop(owner, None)
