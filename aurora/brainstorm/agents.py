"""The Skeptic, Methods, the Librarian and the Mentor: the brainstorm's agents.

Each answers in a conversation and records what it contributes on the board through tools: a question
on an idea, evidence for or against it (a real paper from the indexes, or a run's result), a next step
that builds on it, a one-sentence assessment. The Librarian searches the scholarly indexes; every
reference in an answer is checked like any other.

What an agent may use follows who can see it. In a private brainstorm: the owner's private cards and
everything shared with the project. In a shared brainstorm: only what is shared; the asking person's
private ideas are named to the agent as private, and if one bears on the question it asks that person,
in a message only they see, before using it.

The Mentor is different: it asks, so that the researcher thinks their direction through themselves, and records
nothing on the board; its protocol and what it keeps are in ``mentor.py``.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from aurora.brainstorm.store import bs_store

logger = logging.getLogger(__name__)

AGENTS: dict[str, dict[str, str]] = {
    "Skeptic": {"initials": "SK", "role": "finds the weak points"},
    "Methods": {"initials": "ME", "role": "turns ideas into designs"},
    "Librarian": {"initials": "LI", "role": "brings evidence from the Library"},
    "Mentor": {"initials": "MT", "role": "helps you think it through, by asking"},
}
ACTIVITY = ("off", "moderate", "high")
MAX_ROUNDS = 6

_COMMON = """\
You are the {name} in a research brainstorm in Pippin: a researcher thinks out loud with three agents, \
the Skeptic (finds the weak points), Methods (turns ideas into designs) and the Librarian (brings evidence \
from the literature and the project's runs). You are the {name}: you {role}.

How to answer:
- Briefly: 2 to 5 sentences of plain prose, concrete and specific to this project. No headings, no lists \
unless the researcher asks for one.
- Add something new. Do not repeat what is already on the board or in the conversation.
- Cite papers only by their key in square brackets, e.g. [Behrens12], and only papers from the project's \
literature or from your own searches. Never invent a paper; every reference is checked.
- Record what you contribute on the board with the tools (a question, evidence, a next step, an assessment), \
attached to the idea it concerns. If the researcher's idea is not on the board yet, first write it down \
(add_idea: their idea in one clear sentence, in their terms), then attach your contribution to it. Say in \
your answer what you added. Never talk about your tools or what you cannot do.
- The researcher decides; you advise. Disagree when you have a reason.
- When the researcher asks you to write or rewrite their idea ("write it up", "give me a few versions"), call \
draft_versions with two or three drafts that differ in substance, instead of writing them in your answer: they edit \
any of them in place, take one, or ask for another round from it. Say in a sentence what the drafts vary.
- When a map of the field is given below, place what you say on it: the region of the design space an idea is in, \
the unsolved problem it answers, the nearest papers. If the researcher disputes the map (its definition, a boundary, \
where a paper sits), call revise_map with their point.
{role_rules}"""

ROLE_RULES = {
    "Skeptic": """\
- Find the weakest assumption of the idea under discussion: a confounder, an alternative explanation, a \
measurement problem, a reason it may not generalise. Ask it as one sharp question (add_question).
- If a paper already in the literature or a run's result contradicts the idea, record it as evidence against \
(add_evidence, challenges). Finding new papers is the Librarian's job; designing tests is Methods'.
- When you have search_memory, check the researcher's own memory for what contradicts the idea: their saves and \
notes are the hardest objection to wave away (add_evidence with unit_id, challenges).
- Do not demolish: say what would convince you.""",
    "Methods": """\
- Turn the idea into a test: which data, which comparison, which observable would tell it apart from the \
alternatives, and what result would refute it. Record the design as a next step (add_next_step).
- Prefer designs the project can run with its data and compute; say what it would cost in effort.
- When an idea's open questions are answered and it has evidence, update its assessment (set_assessment).""",
    "Librarian": """\
- When the researcher asks what has been done in a direction, how far a field has come, what the approaches are, or \
whether an idea is new, and no map of that field is given below, propose to map it (map_field): that is how you \
answer a question about a whole field. Say in a sentence why, then let them start it.
- To understand one paper in depth (how its authors came to the idea, its weakest assumption, what to do next), \
propose a deep read (deep_read).
- Search the literature (search_papers) before you answer about evidence; read the abstracts you get.
- Record each relevant paper as evidence for or against the idea (add_evidence with its key), with one \
sentence on what it shows. Say plainly when the evidence is indirect or weak, or when you found none.
- Use the project's runs too: a run's finding can support or challenge an idea (add_evidence with run_id).
- When you have search_memory, look in the researcher's own memory too (what they saved from papers, posts and \
their notes): a claim there is evidence they already hold (add_evidence with unit_id). Say it is theirs.""",
    "Mentor": "",          # its protocol is the ARS Socratic Mentor's (mentor.py)
}

DEEP_RULES = """\
DEEP SEARCH. The researcher asked for a thorough search, as a careful literature review would do it:
- Run many searches (6 to 10) from different angles: the claim itself, its mechanism, competing explanations, \
the methods that would test it, and the closest prior work. Vary the words; follow up on what you find.
- Read the abstracts. Keep what bears on the question; say how each kept paper bears on it.
- Answer with an overview: the most relevant papers first ([Key] and one sentence each on what it shows and how \
directly), then what the literature does not answer yet. Record the most relevant papers as evidence on the \
idea under discussion (add_evidence)."""

_TOOLS_ALL = [
    {"name": "add_question", "description": "Add an open question about an idea (it must be answered before the idea can be tested).",
     "parameters": {"type": "object", "properties": {
         "card_id": {"type": "string", "description": "the idea the question is about"},
         "text": {"type": "string", "description": "the question, one sentence"}}, "required": ["card_id", "text"]}},
    {"name": "add_evidence", "description": "Record evidence for or against an idea: a paper (by key) or a run's result.",
     "parameters": {"type": "object", "properties": {
         "card_id": {"type": "string"},
         "rel": {"type": "string", "enum": ["supports", "challenges"]},
         "paper_key": {"type": "string", "description": "a paper's key from the literature or your searches"},
         "run_id": {"type": "string", "description": "or: a run of this project"},
         "unit_id": {"type": "string", "description": "or: a claim from the researcher's memory (from search_memory)"},
         "text": {"type": "string", "description": "what it shows, one sentence"}}, "required": ["card_id", "rel", "text"]}},
    {"name": "add_next_step", "description": "Add a next step that builds on an idea: a concrete design or a sharper version.",
     "parameters": {"type": "object", "properties": {
         "card_id": {"type": "string"}, "text": {"type": "string", "description": "the step, one or two sentences"}},
         "required": ["card_id", "text"]}},
    {"name": "add_idea", "description": "Add a new idea to the board (only when the conversation produced one nobody has written down).",
     "parameters": {"type": "object", "properties": {
         "text": {"type": "string", "description": "the idea, one or two sentences"},
         "builds_on": {"type": "string", "description": "an idea it builds on, if any"}}, "required": ["text"]}},
    {"name": "read_paper", "description": "Read a paper's PDF (its text, a few pages at a time), when there is one.",
     "parameters": {"type": "object", "properties": {
         "paper_key": {"type": "string"},
         "from_page": {"type": "integer", "description": "the first page to read (1 = the first)"}},
         "required": ["paper_key"]}},
    {"name": "annotate", "description": "Mark a passage of a paper's PDF for the researcher, with what it means for an "
                                        "idea. They see it highlighted in the paper and decide whether to keep it.",
     "parameters": {"type": "object", "properties": {
         "paper_key": {"type": "string"},
         "quote": {"type": "string", "description": "the paper's exact words (10 to 40), copied from read_paper"},
         "comment": {"type": "string", "description": "what it means for the idea, one to three sentences"},
         "card_id": {"type": "string", "description": "the idea it bears on"},
         "rel": {"type": "string", "enum": ["supports", "challenges", "asks about", "builds on"]}},
         "required": ["paper_key", "quote", "comment", "card_id"]}},
    {"name": "map_field", "description": "Propose to map a research direction (why it is needed, what the field means "
                                         "by it, its design space, how far it has come, what is attempted but not solved). "
                                         "The researcher starts it with one click; its steps are told in this conversation.",
     "parameters": {"type": "object", "properties": {
         "topic": {"type": "string", "description": "the direction, in a few words"},
         "brief": {"type": "string", "description": "what the researcher cares about in it, from the conversation"}},
         "required": ["topic"]}},
    {"name": "deep_read", "description": "Propose a deep read of one paper: twelve questions, including how its authors "
                                         "may have come to the idea. The researcher starts it with one click.",
     "parameters": {"type": "object", "properties": {"paper_key": {"type": "string"}}, "required": ["paper_key"]}},
    {"name": "revise_map", "description": "The researcher disputes the map of the field: its definition or a boundary "
                                          "(what: definition), or where a paper sits (what: placement, with paper_key). "
                                          "Works out what it would change; the options come back in this conversation.",
     "parameters": {"type": "object", "properties": {
         "what": {"type": "string", "enum": ["definition", "placement"]},
         "text": {"type": "string", "description": "the researcher's point, in their words"},
         "paper_key": {"type": "string"}}, "required": ["what", "text"]}},
    {"name": "draft_versions", "description": "Write two or three drafts of an idea when the researcher asks you to "
                                              "write or rewrite it. They appear in the conversation; the researcher "
                                              "edits one in place, takes it, or asks for another round from it.",
     "parameters": {"type": "object", "properties": {
         "card_id": {"type": "string", "description": "the idea on the board they are versions of, if it is there"},
         "drafts": {"type": "array", "items": {"type": "string"},
                    "description": "two or three drafts, each self-contained, one to four sentences"}},
         "required": ["drafts"]}},
    {"name": "set_assessment", "description": "Set the one-sentence assessment of where an idea stands.",
     "parameters": {"type": "object", "properties": {
         "card_id": {"type": "string"}, "text": {"type": "string"}}, "required": ["card_id", "text"]}},
]
_SEARCH = {"name": "search_papers", "description": "Search OpenAlex, arXiv, Crossref and Semantic Scholar for papers.",
           "parameters": {"type": "object", "properties": {"query": {"type": "string", "description": "a few specific words"}},
                          "required": ["query"]}}
_MEMORY = {"name": "search_memory", "description": "Search the researcher's own memory: the claims they saved from papers, "
                                         "posts and their notes, each with its sources' words, and the conflicts among them.",
           "parameters": {"type": "object", "properties": {"query": {"type": "string", "description": "what to look for"}},
                          "required": ["query"]}}
_ASK_TO_SHARE = {"name": "ask_to_share", "description": "Ask the researcher, privately, whether one of their private ideas may be used here.",
                 "parameters": {"type": "object", "properties": {
                     "card_id": {"type": "string"}, "why": {"type": "string", "description": "why it bears on the question"}},
                     "required": ["card_id", "why"]}}


@dataclass
class Board:
    """What an agent may see and change, for one turn in one conversation."""

    space: str
    viewer: str                     # who asked
    viewer_name: str
    chat: dict[str, Any]
    cards: list[dict[str, Any]]     # usable cards
    private: list[dict[str, Any]] = field(default_factory=list)    # the viewer's private ideas (shared chats)
    links: list[dict[str, Any]] = field(default_factory=list)
    project: dict[str, Any] = field(default_factory=dict)          # name, objective, runs
    literature: Any = None
    map: dict[str, Any] | None = None                              # the space's map of the field, if there is one

    @property
    def shared_chat(self) -> bool:
        return self.chat["vis"] == "project"

    def card_vis(self) -> str:
        return "project" if self.shared_chat else "private"

    def describe(self, focus: str = "") -> str:
        names = {c["card_id"]: c for c in self.cards}
        lines = []
        if self.project:
            lines.append(f"## Project: {self.project.get('name', '')}\nObjective: {self.project.get('objective', '')}")
            runs = self.project.get("runs") or []
            if runs:
                lines.append("Runs of the project:\n" + "\n".join(f"- {r}" for r in runs[:15]))
        else:
            lines.append("## The researcher's own space (no project)")
        if self.literature is not None:
            items = self.literature.items()[:40]
            if items:
                lines.append("## The project's literature\n" + "\n".join(
                    f"[{e['key']}] {e['paper'].title} — {e['paper'].byline()}" for e in items))
        by_type: dict[str, list[str]] = {}
        for c in self.cards:
            rels = [f"{lk['rel']} {lk['to_card']}" for lk in self.links if lk["from_card"] == c["card_id"]]
            extra = (f" (answer: {c['answer']})" if c["answer"] else "") + (f" [{'; '.join(rels)}]" if rels else "")
            stand = f" Assessment: {c['stand']}" if c["stand"] else ""
            by_type.setdefault(c["type"], []).append(f"- {c['card_id']}: {c['text']}{extra}{stand}")
        if by_type:
            lines.append("## The board\n" + "\n\n".join(f"{t.capitalize()}s:\n" + "\n".join(v) for t, v in by_type.items()))
        else:
            lines.append("## The board\n(empty)")
        if self.private:
            lines.append("## The researcher's private ideas (NOT visible to others here)\nDo not quote or use them in "
                         "this shared conversation. If one bears on the question, call ask_to_share instead.\n"
                         + "\n".join(f"- {c['card_id']}: {c['text']}" for c in self.private))
        if self.map is not None:
            from aurora.review.fieldmap import summary_for_agents
            if self.map["state"] == "ready" or self.map["version"]:
                lines.append(summary_for_agents(self.map))
            else:
                lines.append(f"## A map of the field “{self.map['topic']}” is being built (step: {self.map['step']}).")
        if focus and focus in names:
            lines.append(f"## The conversation is about the idea {focus}: {names[focus]['text']}")
        return "\n\n".join(lines)


@dataclass
class Turn:
    """An agent's answer as it forms: steps shown live, effects on the board, the text."""

    msg_id: str
    steps: list[str] = field(default_factory=list)
    effects: list[list[str]] = field(default_factory=list)

    def step(self, text: str) -> None:
        self.steps.append(text)
        bs_store.update_message(self.msg_id, steps=self.steps)

    def effect(self, what: str, card_id: str) -> None:
        self.effects.append([what, card_id])
        bs_store.update_message(self.msg_id, effects=self.effects)


class BoardTools:
    """The tools' effects on the board, checked against what the agent may touch."""

    def __init__(self, board: Board, agent: str, turn: Turn) -> None:
        self.board, self.agent, self.turn = board, agent, turn
        self.known_papers: dict[str, str] = {}      # key -> paper_id the agent saw

    def _idea(self, card_id: str) -> dict[str, Any]:
        card = next((c for c in self.board.cards if c["card_id"] == card_id), None)
        if card is None:
            raise ValueError(f"no card {card_id} on the board you can use")
        return card

    def _add(self, type_: str, text: str, **kw: Any) -> dict[str, Any]:
        card = bs_store.add_card(self.board.space, self.board.viewer, type_, text, by=self.agent,
                                 vis=self.board.card_vis(), chat_id=self.board.chat["chat_id"], **kw)
        self.board.cards.append(card)
        return card

    async def call(self, name: str, args: dict[str, Any]) -> Any:
        b = self.board
        if name != "ask_to_share" and name not in ROLE_TOOLS.get(self.agent, set()):
            return {"error": f"{name} is not one of your tools"}
        if name == "search_papers":
            query = str(args.get("query", "")).strip()
            if not query or b.literature is None:
                return {"error": "no query"}
            self.turn.step(f"Searching the literature: “{query}”")
            from aurora.literature.service import paper_card
            found = await b.literature.search(query, by=self.agent, context=f"Brainstorm · {b.chat['title']}", limit=6)
            for e in found:
                self.known_papers[e["key"]] = e["paper_id"]
            return "\n".join(paper_card(e, abstract_words=70) for e in found) or "Nothing found."
        if name == "search_memory":
            query = str(args.get("query", "")).strip()
            if not query or b.shared_chat:
                return {"error": "the researcher's memory is not used in a shared conversation"}
            self.turn.step(f"Looking in your notebook: “{query}”")
            return await search_memory(b.viewer, query)
        if name == "add_question":
            idea = self._idea(str(args.get("card_id")))
            q = self._add("question", str(args.get("text", "")))
            bs_store.link(q["card_id"], idea["card_id"], "asks about")
            self.turn.effect("Added a question to", idea["card_id"])
            return {"ok": True, "card_id": q["card_id"]}
        if name == "add_evidence":
            idea = self._idea(str(args.get("card_id")))
            rel = "challenges" if args.get("rel") == "challenges" else "supports"
            text = str(args.get("text", ""))
            if args.get("paper_key"):
                from aurora.literature.library import library
                key = str(args["paper_key"]).strip("[] ")
                entry = library.paper(key)
                known = library.known_to(b.literature.campaign_id) if b.literature is not None else set()
                if entry is None or (entry["paper_id"] not in known and key not in self.known_papers):
                    return {"error": f"[{key}] is not a paper from the literature or your searches"}
                self.turn.step(f"Reading [{entry['key']}]")
                card = self._add("material", f"{entry['paper'].title}. {text}".strip(), paper_id=entry["paper_id"])
                if b.literature is not None:
                    b.literature._cited(entry["paper_id"], by=self.agent, place=f"Brainstorm ({b.chat['title'][:40]})")
                    if b.space.startswith("user:"):
                        library.add_item("", entry["paper_id"], source="agent", added_by=self.agent, owner=b.viewer)
            elif args.get("unit_id"):
                from aurora.memory.store import mem_store
                u = mem_store.unit(str(args["unit_id"]))
                if u is None or u["owner"] != b.viewer or b.shared_chat:
                    return {"error": "not a claim from the researcher's memory that may be used here"}
                card = memory_card(b.space, b.viewer, u, text, by=self.agent, vis=b.card_vis(), chat_id=b.chat["chat_id"])
                b.cards.append(card)
            elif args.get("run_id"):
                self.turn.step(f"Recalling run {args['run_id']}")
                card = self._add("material", text, run_id=str(args["run_id"]))
            else:
                return {"error": "give paper_key or run_id"}
            bs_store.link(card["card_id"], idea["card_id"], rel)
            self.turn.effect("Added evidence for" if rel == "supports" else "Added evidence against", idea["card_id"])
            return {"ok": True}
        if name == "add_next_step":
            idea = self._idea(str(args.get("card_id")))
            step = self._add("idea", str(args.get("text", "")))
            bs_store.link(step["card_id"], idea["card_id"], "builds on")
            self.turn.effect("Added a next step to", idea["card_id"])
            return {"ok": True, "card_id": step["card_id"]}
        if name == "add_idea":
            idea = self._add("idea", str(args.get("text", "")))
            if args.get("builds_on"):
                try:
                    bs_store.link(idea["card_id"], self._idea(str(args["builds_on"]))["card_id"], "builds on")
                except ValueError:
                    pass
            self.turn.effect("Started an idea", idea["card_id"])
            return {"ok": True, "card_id": idea["card_id"]}
        if name in ("read_paper", "annotate"):
            return await self._paper_tool(name, args)
        if name in ("map_field", "deep_read", "revise_map"):
            return await self._review_tool(name, args)
        if name == "draft_versions":
            return self._drafts(args)
        if name == "set_assessment":
            idea = self._idea(str(args.get("card_id")))
            bs_store.update_card(idea["card_id"], stand=str(args.get("text", ""))[:600])
            self.turn.effect("Updated the assessment of", idea["card_id"])
            return {"ok": True}
        if name == "ask_to_share":
            card = next((c for c in b.private if c["card_id"] == args.get("card_id")), None)
            if card is None or not b.shared_chat:
                return {"error": "not a private idea of the researcher"}
            bs_store.add_message(b.chat["chat_id"], self.agent, f"{args.get('why', '')}".strip(), kind="consent",
                                 only_for=b.viewer, consent=card["card_id"])
            return {"ok": True, "note": "Asked the researcher privately; do not use the idea until they share it."}
        return {"error": f"unknown tool {name}"}


    def _drafts(self, args: dict[str, Any]) -> Any:
        from aurora.brainstorm import ideas, mentor
        b = self.board
        drafts = [str(d) for d in args.get("drafts") or [] if str(d).strip()]
        if len(drafts) < 2:
            return {"error": "give two or three drafts"}
        card_id = str(args.get("card_id") or "")
        if card_id and not any(c["card_id"] == card_id for c in b.cards):
            card_id = ""
        if self.agent == "Mentor":        # model-led work leaves guided questioning, visibly
            chat = bs_store.chat(b.chat["chat_id"])
            if chat is not None:
                g = mentor.guide_of(chat)
                if not g["suggesting"]:
                    g["suggesting"] = True
                    bs_store.set_guide(chat["chat_id"], g)
                    self.turn.effect(mentor.EXIT, "")
        yours = ""
        for m in reversed(bs_store.messages(b.chat["chat_id"])):      # another round: their version comes first
            w = m.get("widget") or {}
            if w.get("type") == "drafts" and w.get("purpose", "idea") == "idea":
                if w.get("state") == "taken" and w.get("again") and not w.get("followed"):
                    yours, card_id = w.get("text", ""), card_id or w.get("card_id", "")
                    bs_store.update_message(m["msg_id"], widget={**w, "followed": True})
                break
        ideas.post_drafts(b.chat, self.agent, drafts, card_id=card_id, yours=yours)
        self.turn.step("Writing drafts")
        return {"ok": True, "note": "The drafts are in the conversation; the researcher edits, takes one, or asks for "
                                    "another round. Do not repeat them in your answer."}

    async def _review_tool(self, name: str, args: dict[str, Any]) -> Any:
        """Zoom out and zoom in from the conversation: proposals the researcher starts, revisions of the map."""
        b = self.board
        chat = b.chat["chat_id"]
        if name == "map_field":
            topic = " ".join(str(args.get("topic", "")).split())[:200]
            if not topic:
                return {"error": "name the direction"}
            if b.map is not None and b.map["state"] == "building":
                return {"error": f"a map of “{b.map['topic']}” is being built; wait for it"}
            from aurora.review.converse import estimate_calls
            calls = estimate_calls(30)
            bs_store.add_message(chat, self.agent, f"Map the field: {topic}. About {calls} model calls; a few minutes.",
                                 kind="action", widget={"type": "proposal", "action": "map_field", "topic": topic,
                                                        "brief": str(args.get("brief", ""))[:1000], "size": 30,
                                                        "calls": calls, "state": "pending"})
            return {"ok": True, "note": "Proposed; the researcher starts it from the button under your message."}
        if name == "deep_read":
            from aurora.literature.library import library
            entry = library.paper(str(args.get("paper_key", "")).strip("[] "))
            if entry is None:
                return {"error": "not a paper Pippin knows; give its key"}
            bs_store.add_message(chat, self.agent, f"Deep-read [{entry['key']}]: twelve questions, about fourteen model calls.",
                                 kind="action", widget={"type": "proposal", "action": "deep_read", "paper_id": entry["paper_id"],
                                                        "key": entry["key"], "title": entry["paper"].title, "state": "pending"})
            return {"ok": True, "note": "Proposed; the researcher starts it from the button under your message."}
        # revise_map
        if b.map is None:
            return {"error": "there is no map of the field in this space"}
        if b.map["owner"] != b.viewer:
            return {"error": "only the map's owner changes it; say so to the researcher"}
        from fastapi import HTTPException

        from aurora.api.routes import review as rv
        from aurora.review.store import review_store
        review_store.set_state(b.map["map_id"], chat_id=chat)        # the conversation about the map is here now
        try:
            if args.get("what") == "placement":
                from aurora.literature.library import library
                entry = library.paper(str(args.get("paper_key", "")).strip("[] "))
                if entry is None:
                    return {"error": "give the key of the paper whose placement is disputed"}
                rv.start_replace(b.map["map_id"], entry["paper_id"], str(args.get("text", "")))
                self.turn.step(f"Placing [{entry['key']}] again")
                return {"ok": True, "note": "The paper is being placed again with the researcher's point."}
            rv.start_challenge(b.map["map_id"], str(args.get("text", "")))
        except HTTPException as exc:
            return {"error": exc.detail}
        self.turn.step("Working out what the challenge would change")
        return {"ok": True, "note": "Working out what it would change; the options come in this conversation."}

    async def _pdf_pages(self, key: str) -> tuple[dict[str, Any], list[str]]:
        from aurora.literature.library import library
        from aurora.literature.reading import fetch_open_pdf, find_pdf, page_texts
        b = self.board
        entry = library.paper(key.strip("[] "))
        if entry is None:
            raise ValueError(f"[{key}] is not a paper Pippin knows")
        known = library.known_to(b.literature.campaign_id) if b.literature is not None else set()
        if entry["paper_id"] not in known and entry["key"] not in self.known_papers:
            raise ValueError(f"[{entry['key']}] is not a paper from the literature or your searches")
        campaign_id = "" if b.space.startswith("user:") else b.space
        pdf = find_pdf(entry["paper_id"], b.viewer, campaign_id)
        if pdf is None:
            # not here yet: its open copy (arXiv, or the PDF an index links), fetched once for everyone
            self.turn.step(f"Fetching the PDF of [{entry['key']}]")
            pdf = await fetch_open_pdf(entry["paper_id"])
        if pdf is None:
            raise ValueError(f"there is no PDF of [{entry['key']}] here; work from its abstract")
        return entry, page_texts(pdf)

    async def _paper_tool(self, name: str, args: dict[str, Any]) -> Any:
        entry, pages = await self._pdf_pages(str(args.get("paper_key", "")))
        if name == "read_paper":
            start = max(1, int(args.get("from_page") or 1))
            self.turn.step(f"Reading [{entry['key']}], from page {start}")
            out, total = [], 0
            for i in range(start - 1, len(pages)):
                chunk = f"--- page {i + 1} of {len(pages)} ---\n{pages[i].strip()}"
                if total + len(chunk) > 24_000 and out:
                    out.append(f"(more from page {i + 1})")
                    break
                out.append(chunk)
                total += len(chunk)
            return "\n".join(out) or "The PDF has no text on those pages."
        from aurora.literature.annotations import annotations, find_page
        idea = self._idea(str(args.get("card_id")))
        quote = " ".join(str(args.get("quote", "")).split())
        page = find_page(pages, quote)
        if page < 0:
            return {"error": "the paper does not contain these exact words; copy the quote from read_paper"}
        rel = str(args.get("rel", ""))
        annotations.add(self.board.viewer, entry["paper_id"], author=self.agent, state="suggested", text=quote,
                        comment=str(args.get("comment", ""))[:1500], page_index=page, page_label=str(page + 1),
                        space=self.board.space, card_id=idea["card_id"], rel=rel, chat_id=self.board.chat["chat_id"])
        self.turn.effect(f"Marked a passage of [{entry['key']}] for", idea["card_id"])
        return {"ok": True, "note": "Marked; the researcher sees it in the paper as your suggestion."}


# each agent records what its role brings, so they do not do each other's work
ROLE_TOOLS = {
    # every agent may write down the researcher's idea when it is not on the board yet
    "Skeptic": {"add_question", "add_evidence", "add_idea", "set_assessment", "read_paper", "annotate", "revise_map",
                "draft_versions", "search_memory"},
    "Methods": {"add_next_step", "add_question", "add_idea", "set_assessment", "read_paper", "annotate", "revise_map",
                "draft_versions"},
    "Librarian": {"search_papers", "add_evidence", "add_question", "add_idea", "read_paper", "annotate", "map_field",
                  "deep_read", "revise_map", "draft_versions", "search_memory"},
    # a counterpoint, a piece of knowledge or an analogy from the literature, checked in the paper itself when the
    # abstract does not say; drafts only when asked, visibly
    "Mentor": {"search_papers", "read_paper", "draft_versions"},
}


def tool_specs(agent: str, board: Board) -> list[dict[str, Any]]:
    allowed = ROLE_TOOLS.get(agent, set())
    if board.map is None:
        allowed = allowed - {"revise_map"}
    specs = [t for t in _TOOLS_ALL if t["name"] in allowed]
    if board.literature is not None and "search_papers" in allowed:
        specs.append(_SEARCH)
    if board.shared_chat and board.private:
        specs.append(_ASK_TO_SHARE)
    if "search_memory" in allowed and not board.shared_chat and _has_memory(board.viewer):
        specs.append(_MEMORY)       # the notebook is personal: only in the researcher's own conversations
    return [{"type": "function", "function": s} for s in specs]


# makes the OpenAI-compatible client for a model access (None: the real one); tests put a fake here
client_factory: Any = None


def _client(access: ModelAccess) -> Any:
    if client_factory is not None:
        return client_factory(access)
    from openai import AsyncOpenAI
    return AsyncOpenAI(api_key=access.api_key, base_url=access.base_url)


@dataclass
class ModelAccess:
    api_key: str
    base_url: str
    model: str
    # what to do about a missing key, as the person asking can do it
    missing: str = "add yours under the account button → Your API keys, then ask again."


async def answer(agent: str, board: Board, history: list[dict[str, Any]], question: str, access: ModelAccess,
                 *, focus: str = "", client: Any = None, deep: bool = False, context: str = "") -> dict[str, Any]:
    """Run one agent's turn: post a pending message, think with tools, then the answer.

    ``deep``: the Librarian's thorough search; ``context``: what the researcher attached (papers, files).
    """
    msg = bs_store.add_message(board.chat["chat_id"], agent, "", steps=["Thinking"], pending=True)
    turn = Turn(msg["msg_id"], steps=["Thinking"])
    tools = BoardTools(board, agent, turn)
    if not access.api_key:
        bs_store.update_message(msg["msg_id"], text=f"No API key is set: {access.missing}", pending=False)
        return bs_store.message(msg["msg_id"])  # type: ignore[return-value]
    from aurora.brainstorm import mentor
    if agent == "Mentor":
        system = mentor.system_prompt(board)
    else:
        system = _COMMON.format(name=agent, role=AGENTS[agent]["role"], role_rules=ROLE_RULES[agent])
    if deep:
        system += "\n\n" + DEEP_RULES
    messages: list[dict[str, Any]] = [{"role": "system", "content": system},
                                      {"role": "user", "content": board.describe(focus)}]
    for h in history[-30:]:
        if h["kind"] != "text" or h["pending"] or not h["text"]:
            continue
        who = h["who"]
        speaker = who if who in AGENTS else ("The researcher" if who == board.viewer else "A colleague")
        messages.append({"role": "assistant" if who == agent else "user", "content": f"{speaker}: {h['text']}"})
    if context:
        messages.append({"role": "user", "content": f"What {board.viewer_name} attached:\n\n{context}"})
    if agent == "Mentor":
        messages.append({"role": "user", "content": mentor.state_note(board.chat, question)})
        messages.append({"role": "user", "content": f"{board.viewer_name}: {question}"})
    else:
        messages.append({"role": "user", "content": f"{board.viewer_name} asks you: {question}"})
    client = client or _client(access)
    text = ""
    try:
        for _ in range(MAX_ROUNDS * 2 if deep else MAX_ROUNDS):
            resp = await client.chat.completions.create(model=access.model, messages=messages,
                                                        tools=tool_specs(agent, board), temperature=0.4, max_tokens=4000)
            choice = resp.choices[0].message
            calls = getattr(choice, "tool_calls", None) or []
            if not calls:
                text = (choice.content or "").strip()
                break
            messages.append({"role": "assistant", "content": choice.content or "",
                             "tool_calls": [{"id": c.id, "type": "function",
                                             "function": {"name": c.function.name, "arguments": c.function.arguments}}
                                            for c in calls]})
            for c in calls:
                try:
                    args = json.loads(c.function.arguments or "{}")
                    result = await tools.call(c.function.name, args)
                except Exception as exc:          # a bad call is the agent's to fix, not the turn's end
                    result = {"error": str(exc)}
                messages.append({"role": "tool", "tool_call_id": c.id,
                                 "content": result if isinstance(result, str) else json.dumps(result)})
        else:
            text = "I ran out of steps before finishing; ask me again more narrowly."
    except Exception as exc:
        logger.warning("Brainstorm agent %s failed", agent, exc_info=True)
        text = f"I could not answer: {str(exc)[:300]}"
    if agent == "Mentor" and text:
        text, extra = mentor.after_turn(board.chat["chat_id"], board.viewer, text, console=question.startswith("(Console:"))
        for e in extra:
            turn.effect(*e)
    # references in the answer are checked like any agent's
    if board.literature is not None and text:
        try:
            cites = await board.literature.check_text(text, by=agent, place=f"Brainstorm ({board.chat['title'][:40]})")
            if not cites.clean:
                text += "\n\n" + cites.note()
        except Exception:
            logger.debug("checking a brainstorm answer failed", exc_info=True)
    bs_store.update_message(msg["msg_id"], text=text or "(no answer)", pending=False)
    return bs_store.message(msg["msg_id"])  # type: ignore[return-value]


_MENTION = re.compile(r"@(Skeptic|Method|Librarian|Mentor)s?\b", re.I)      # @Skeptics, @Method: close enough


def mentioned(text: str) -> list[str]:
    seen: list[str] = []
    for m in _MENTION.findall(text or ""):
        name = "Methods" if m.lower() == "method" else m.capitalize()
        if name not in seen:
            seen.append(name)
    return seen


async def pick(text: str, activity: str, access: ModelAccess, client: Any = None, *, opening: bool = False,
               fallback: str = "Librarian") -> list[str]:
    """Who answers a message nobody was named in.

    * *opening* (the first message of a brainstorm), or "very" active: always one or two;
    * moderately active: one, unless the researcher is plainly in the middle of a train of thought
      (greetings, questions and anything said to the room get an answer).

    When one must answer and the model names nobody, *fallback* does (the agent who spoke last)."""
    if activity == "off" or not access.api_key:
        return []
    n = 2 if activity == "high" else 1
    must = opening or activity == "high"
    agents = ("Skeptic (finds weak points), Methods (turns ideas into tests, and writes versions of an idea when "
              "asked to write or rewrite it), Librarian (finds what is known "
              "in the literature, maps a field, reads a paper closely), Mentor (helps the researcher think a direction "
              "through by asking questions, without proposing ideas: for a vague or early interest, a direction they are "
              "unsure about, wanting to sharpen their own research question, or asking to be guided or challenged to "
              "think)")
    if must:
        lead = (f"A researcher opened a brainstorm with: \"{text[:1500]}\"" if opening
                else f"A researcher wrote in a brainstorm: \"{text[:1500]}\"")
        prompt = (f"{lead}\n\nWhich of these agents should answer? {agents}. Answer with up to {n} names "
                  f"separated by commas, most useful first.")
    else:
        prompt = (f"A researcher wrote in a brainstorm: \"{text[:1500]}\"\n\nWhich of these agents should "
                  f"answer? {agents}. Answer with up to {n} names separated by commas, most useful first. Answer "
                  f"NONE only if the researcher is plainly in the middle of their own train of thought and a reply "
                  f"would interrupt; a greeting, a question, or anything said to the agents gets an answer.")
    client = client or _client(access)
    try:
        resp = await client.chat.completions.create(model=access.model, temperature=0,
                                                    # reasoning models think first: leave room for the answer
                                                    messages=[{"role": "user", "content": prompt}], max_tokens=600)
        out = resp.choices[0].message.content or ""
    except Exception:
        logger.debug("picking a brainstorm agent failed", exc_info=True)
        return []
    found = [(m.start(), a) for a in AGENTS if (m := re.search(rf"\b{a}\b", out, re.I))]
    names = [a for _, a in sorted(found)][:n]
    return names or ([fallback if fallback in AGENTS else "Librarian"] if must else [])


# ---------------------------------------------------------------------------
# The researcher's memory, as evidence on the board


def _has_memory(owner: str) -> bool:
    try:
        from aurora.memory.store import mem_store
        return bool(mem_store.units(owner))
    except Exception:
        return False


async def search_memory(owner: str, query: str, limit: int = 8) -> str:
    """The researcher's claims nearest to a query, with their sources' words and conflicts (for an agent)."""
    from aurora.memory import ingest
    from aurora.memory.follow import _access_of
    from aurora.memory.store import mem_store
    units = mem_store.units(owner)
    if not units:
        return "Their notebook is empty."
    try:
        access, models = _access_of(owner)
    except Exception as exc:
        return f"Their notebook cannot be searched now: {exc}"
    vec = (await ingest.embed(access, models["embedding"], [query]))[0]
    uv = await ingest.unit_vectors(access, models["embedding"], units)
    near = [u for u in sorted(units, key=lambda u: -ingest.cosine(vec, uv.get(u["unit_id"], [0.0])))[:limit]
            if ingest.relevance(ingest.cosine(vec, uv.get(u["unit_id"], [0.0]))) >= 0.3]
    if not near:
        return "Nothing in their notebook bears on it."
    out = []
    for u in near:
        ev = mem_store.evidence(u["unit_id"])
        quotes = "; ".join(f"“{e['quote'][:160]}” ({e['title'] or e['platform']})" for e in ev[:3])
        clash = [lk for lk in mem_store.links([u["unit_id"]]) if lk["rel"] == "contradicts"]
        out.append(f"[{u['unit_id']}] {u['claim']} — {len({e['source_id'] for e in ev})} source(s): {quotes}"
                   + (f" — conflicts with {len(clash)} other claim(s)" if clash else ""))
    return "\n".join(out)


def memory_card(space: str, owner: str, u: dict[str, Any], text: str = "", *, by: str = "", vis: str = "private",
                chat_id: str = "") -> dict[str, Any]:
    """A claim from the researcher's memory as material on a brainstorm board (it keeps its unit, and its sources)."""
    from aurora.memory.store import mem_store
    ev = mem_store.evidence(u["unit_id"])
    n = len({e["source_id"] for e in ev})
    url = next((e["url"] for e in ev if e["url"] and not e["url"].startswith("obsidian://")), "")
    own = any(e["platform"] == "note" for e in ev)
    body = u["claim"] + (f" {text}" if text and text.strip() != u["claim"] else "")
    return bs_store.add_card(space, owner, "material", body, by=by or owner, vis=vis, chat_id=chat_id,
                             source=f"From your notebook · {n} source{'s' if n != 1 else ''}" + (" · your note" if own else ""),
                             url=url, unit_id=u["unit_id"])
