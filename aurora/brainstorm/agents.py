"""The Skeptic, Methods and the Librarian: the brainstorm's agents.

Each answers in a conversation and records what it contributes on the board through tools: a question
on an idea, evidence for or against it (a real paper from the indexes, or a run's result), a next step
that builds on it, a one-sentence assessment. The Librarian searches the scholarly indexes; every
reference in an answer is checked like any other.

What an agent may use follows who can see it. In a private brainstorm: the owner's private cards and
everything shared with the project. In a shared brainstorm: only what is shared; the asking person's
private ideas are named to the agent as private, and if one bears on the question it asks that person,
in a message only they see, before using it.
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
}
ACTIVITY = ("off", "moderate", "high")
MAX_ROUNDS = 6

_COMMON = """\
You are the {name} in a research brainstorm in AURORA: a researcher thinks out loud with three agents, \
the Skeptic (finds the weak points), Methods (turns ideas into designs) and the Librarian (brings evidence \
from the literature and the project's runs). You are the {name}: you {role}.

How to answer:
- Briefly: 2 to 5 sentences of plain prose, concrete and specific to this project. No headings, no lists \
unless the researcher asks for one.
- Add something new. Do not repeat what is already on the board or in the conversation.
- Cite papers only by their key in square brackets, e.g. [Behrens12], and only papers from the project's \
literature or from your own searches. Never invent a paper; every reference is checked.
- Record what you contribute on the board with the tools (a question, evidence, a next step, an assessment), \
attached to the idea it concerns. Say in your answer what you added.
- The researcher decides; you advise. Disagree when you have a reason.
{role_rules}"""

ROLE_RULES = {
    "Skeptic": """\
- Find the weakest assumption of the idea under discussion: a confounder, an alternative explanation, a \
measurement problem, a reason it may not generalise. Ask it as one sharp question (add_question).
- If a paper already in the literature or a run's result contradicts the idea, record it as evidence against \
(add_evidence, challenges). Finding new papers is the Librarian's job; designing tests is Methods'.
- Do not demolish: say what would convince you.""",
    "Methods": """\
- Turn the idea into a test: which data, which comparison, which observable would tell it apart from the \
alternatives, and what result would refute it. Record the design as a next step (add_next_step).
- Prefer designs the project can run with its data and compute; say what it would cost in effort.
- When an idea's open questions are answered and it has evidence, update its assessment (set_assessment).""",
    "Librarian": """\
- Search the literature (search_papers) before you answer about evidence; read the abstracts you get.
- Record each relevant paper as evidence for or against the idea (add_evidence with its key), with one \
sentence on what it shows. Say plainly when the evidence is indirect or weak, or when you found none.
- Use the project's runs too: a run's finding can support or challenge an idea (add_evidence with run_id).""",
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
         "text": {"type": "string", "description": "what it shows, one sentence"}}, "required": ["card_id", "rel", "text"]}},
    {"name": "add_next_step", "description": "Add a next step that builds on an idea: a concrete design or a sharper version.",
     "parameters": {"type": "object", "properties": {
         "card_id": {"type": "string"}, "text": {"type": "string", "description": "the step, one or two sentences"}},
         "required": ["card_id", "text"]}},
    {"name": "add_idea", "description": "Add a new idea to the board (only when the conversation produced one nobody has written down).",
     "parameters": {"type": "object", "properties": {
         "text": {"type": "string", "description": "the idea, one or two sentences"},
         "builds_on": {"type": "string", "description": "an idea it builds on, if any"}}, "required": ["text"]}},
    {"name": "set_assessment", "description": "Set the one-sentence assessment of where an idea stands.",
     "parameters": {"type": "object", "properties": {
         "card_id": {"type": "string"}, "text": {"type": "string"}}, "required": ["card_id", "text"]}},
]
_SEARCH = {"name": "search_papers", "description": "Search OpenAlex, arXiv, Crossref and Semantic Scholar for papers.",
           "parameters": {"type": "object", "properties": {"query": {"type": "string", "description": "a few specific words"}},
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


# each agent records what its role brings, so they do not do each other's work
ROLE_TOOLS = {
    "Skeptic": {"add_question", "add_evidence", "set_assessment"},
    "Methods": {"add_next_step", "add_question", "add_idea", "set_assessment"},
    "Librarian": {"search_papers", "add_evidence", "add_question"},
}


def tool_specs(agent: str, board: Board) -> list[dict[str, Any]]:
    allowed = ROLE_TOOLS.get(agent, set())
    specs = [t for t in _TOOLS_ALL if t["name"] in allowed]
    if board.literature is not None and "search_papers" in allowed:
        specs.append(_SEARCH)
    if board.shared_chat and board.private:
        specs.append(_ASK_TO_SHARE)
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


_MENTION = re.compile(r"@(Skeptic|Methods|Librarian)\b", re.I)


def mentioned(text: str) -> list[str]:
    seen: list[str] = []
    for m in _MENTION.findall(text or ""):
        name = m.capitalize()
        if name not in seen:
            seen.append(name)
    return seen


async def pick(text: str, activity: str, access: ModelAccess, client: Any = None) -> list[str]:
    """Who answers a message nobody was named in: none, one, or (very active) up to two agents."""
    if activity == "off" or not access.api_key:
        return []
    n = 2 if activity == "high" else 1
    prompt = (f"A researcher wrote in a brainstorm: \"{text[:1500]}\"\n\nWhich of these agents should answer, "
              f"if any? Skeptic (finds weak points), Methods (turns ideas into tests), Librarian (finds evidence in "
              f"the literature). Answer with up to {n} names separated by commas, most useful first, or NONE if "
              f"the researcher is just thinking out loud and an answer would interrupt.")
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
    return [a for _, a in sorted(found)][:n]
