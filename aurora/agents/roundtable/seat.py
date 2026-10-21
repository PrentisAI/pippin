"""Roundtable seat — one researcher at the group meeting (Section 2.4, M.1).

Each seat is a different foundation model. Over the meeting a seat:
  prepares   — looks into the literature and prepares a short presentation with one main idea;
  remarks    — on others' presentations, asks questions, raises concerns, suggests changes, points to
               evidence, builds on ideas, or agrees with a reason; or passes;
  responds   — answers the remarks on its own presentation, accepting or rejecting each with reasons;
  writes     — if its direction is chosen, writes the final proposal.
Every turn sees the full meeting transcript; nothing is truncated.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, Field

from aurora.agents.base import (
    Agent,
    AgentAction,
    AgentActionType,
    AgentContext,
    AgentRole,
)
from aurora.config import PipelineStage
from aurora.llm.base import LLMResponse, Message
from aurora.llm.router import LLMRouter
from aurora.models.roundtable import SeatContribution

logger = logging.getLogger(__name__)


SEAT_SYSTEM_PROMPT = """\
You are {seat_id}, one of several researchers at a research group meeting. The other participants \
have different backgrounds (they are different foundation models). The meeting decides what research \
to do next on the topic in the briefing.

## How to take part
Take part as you would in a good lab group meeting: ask questions when something is unclear, raise \
concerns and doubts, suggest changes or alternatives, point to relevant evidence, build on other \
people's ideas, and say when you agree and why. Be specific and constructive. Only say what adds \
something; it is fine to pass.

A good group meeting makes ideas better, not smaller. Help each idea reach the most important version \
of it that can still be done: point out what is wrong, and also say how it could tell us more. Checking \
a single citation or accession number is the presenter's homework; mention it briefly and spend the \
discussion on the science.

## Evidence discipline
Ground claims in: (1) experiment results in the briefing (cite the idea_id and the finding); \
(2) mental-model beliefs in the briefing (cite the belief); (3) published literature or data you \
actually found ({cite_how}). Anything beyond these must be flagged '(my claim, unverified)'. \
Keep what sources say separate from your own reasoning.{search_note}{papers_note}

You're welcome to bring relevant knowledge, methods and ideas from adjacent fields---or even distant \
but structurally related ones---to bear on this question."""

SEARCH_NOTE = (" When preparing and when answering remarks on your own work you can search the web; use it to "
               "check what is already known and to verify claims.")
SEARCH_TURNS = {"presentation", "response"}

PAPERS_NOTE = """

## Papers
Before you write a presentation or answer remarks on it, AURORA searches the scholarly indexes (OpenAlex, \
arXiv, Crossref, Semantic Scholar) with queries you choose and gives you what it found. Cite a paper from \
those results or from the project's literature by its key in square brackets, e.g. [Behrens12]. Cite any \
other paper as [ref: title; first author; year; DOI or arXiv ID if you know it]. Every reference is \
checked against the indexes; one that is not found is marked unsupported for everyone at the meeting, \
so cite only papers you have reason to think exist and say what they show."""

QUERIES = """You are about to write your {what}. Which published work do you need to look at first: what is already \
known, the closest prior work, methods or data you would use, evidence for or against a claim? Reply \
with up to {n} search queries for a scholarly search engine, one per line, each starting with \
'SEARCH: ' (a few specific words, as you would type them into Google Scholar). Reply NONE if you need \
nothing new."""

_QUERY = re.compile(r"^\s*SEARCH:\s*(.+?)\s*$", re.I | re.M)

PREPARE = """\
BEFORE THE MEETING. Look into what is already known about the topic, then prepare a short presentation:
1. Your understanding of the question and where the real gap is.
2. What prior work has established, with sources.
3. ONE main research idea: the question it answers, the hypothesis, the method (concrete observables, \
data, comparisons), what result would refute it, why it matters, and whether it can be done in the \
setting described.
4. Briefly, one or two alternatives you considered and why you prefer the main idea."""

REMARKS = """\
AGENDA ITEM: {presenter}'s presentation{followup}. Respond as you would at a group meeting. Make each \
remark a short paragraph starting with one tag: [QUESTION], [CONCERN], [SUGGESTION], [EVIDENCE], \
[BUILD-ON] or [AGREE] (agreement only with a reason). If you have nothing that adds to the discussion, \
reply only PASS. End with a line 'HAND: yes' if you expect to want to follow up after {presenter} \
answers, otherwise 'HAND: no'."""

SELF_REMARKS = """\
SELF-REVIEW. Read your presentation again as a demanding colleague would at a group meeting: point out \
what is wrong, and also how the idea could tell us more. Make each remark a short paragraph starting with \
one tag: [QUESTION], [CONCERN], [SUGGESTION] or [EVIDENCE]."""

RESPOND = """\
YOUR ITEM. Colleagues have made remarks on your presentation (see the transcript). Answer each \
question. Start your answer to each concern or suggestion with ACCEPT, REBUT or DEFER: accept it only if \
it makes the research better and say how the plan changes; rebut it, with a reason or evidence, if you \
think it is mistaken or would dilute the idea; defer it if it is worth doing but not now. Accepting \
every remark is not a virtue: a plan that absorbs every suggestion loses its focus. If something needs checking, check it now if you can. Fix weaknesses by changing the design \
(controls, checks, better data) rather than by retreating to a smaller or already-answered question, \
unless the original cannot be done. End with a short paragraph 'Updated plan:' if your plan changed."""

OPEN = """\
OPEN DISCUSSION. All presentations have been discussed. Raise anything that cuts across them: ideas \
that could be combined, a new idea the discussion sparked, risks they share, or anything still \
unresolved. Use the same tags as before, or reply only PASS."""

OPEN_REPLY = """\
OPEN DISCUSSION, continued. Respond to remarks in the open discussion that concern your work or that \
you have something to add to. Use the same tags, or reply only PASS."""

WRITE = """\
The meeting is over and the chair has decided the direction (decision below). Write the final research \
proposal for this direction. Incorporate what was accepted in the meeting; do not add new directions. \
Keep the scientific ambition of the direction: concerns are addressed through the design, not by \
shrinking the question. Keep within the word limits given for each field.

## Chair's decision
{decision}"""

WRITE_ALONE = """\
Write the final research proposal for your main idea, incorporating anything you revised along the way. \
Keep within the word limits given for each field."""

STREAM_INTERVAL = 1.0  # seconds between live updates of a turn being written

_UNVERIFIED = re.compile(r"[^.\n]*\(my claim, unverified\)", re.I)
_PASS = re.compile(r"^\W*pass\W*$", re.I)
_HAND = re.compile(r"HAND:\s*(yes|no)", re.I)


class FinalProposal(BaseModel):
    """The final proposal, in the same fields as a single agent's ideation record."""

    title: str = Field(description="Short title (max 15 words)")
    problem: str = Field(description="The research question the proposal answers (max 80 words)")
    hypothesis: str = Field(description="The hypothesis to test (max 120 words)")
    method: str = Field(description="Method: concrete observables, data, comparisons, analyses (max 250 words)")
    # no defaults, so every field is "required" in the JSON schema and models do not omit it
    alternatives_considered: str = Field(
        description="Other directions considered and why this one was preferred (max 100 words)"
    )
    falsification_check: str = Field(description="What result would refute the hypothesis (max 100 words)")
    expected_outcomes: list[str] = Field(description="At most 3 items")
    unresolved_disagreements: list[str] = Field(description="At most 5 items; empty list if none")


WORD_LIMITS = {"title": 15, "problem": 80, "hypothesis": 120, "method": 250,
               "alternatives_considered": 100, "falsification_check": 100}


class _LiveTurn:
    """Reports a turn while it is being written: its text so far, at most every STREAM_INTERVAL s."""

    def __init__(self, progress: Callable[..., Awaitable[None]], turn: dict) -> None:
        self._progress = progress
        self._turn = turn
        self._text: list[str] = []
        self._thinking: list[str] = []
        self._last = 0.0

    async def start(self) -> None:
        await self._send()

    async def add(self, kind: str, piece: str) -> None:
        (self._thinking if kind == "reasoning" else self._text).append(piece)
        if time.monotonic() - self._last >= STREAM_INTERVAL:
            await self._send()

    async def fail(self, exc: Exception) -> None:
        try:
            await self._progress("ideation_turn", **self._turn, content="", error=str(exc)[:300])
        except Exception:
            logger.debug("ideation_turn callback failed", exc_info=True)

    async def _send(self) -> None:
        self._last = time.monotonic()
        try:
            # the whole text so far, so a viewer who connects mid-turn sees all of it;
            # of the reasoning only its end, to show what the model is thinking about now
            await self._progress("ideation_stream", **self._turn, text="".join(self._text),
                                 thinking="".join(self._thinking)[-600:])
        except Exception:
            logger.debug("ideation_stream callback failed", exc_info=True)


_TITLE = re.compile(r"^\W*title\W*:\s*(.+)", re.I)
_MAIN_IDEA = re.compile(r"main\b.*\bidea|research idea", re.I)
_LABEL = re.compile(r"^[\W\d]*(question|research question|idea)\W*:\W*(.+)", re.I)
_MARKUP = re.compile(r"^[#>*\-\s\d.)]+|[*_`]+")


def _plain(line: str) -> str:
    return _MARKUP.sub("", line).strip(" :*-")


def headline(text: str, limit: int = 160) -> str:
    """A one-line title for a presentation: its stated title, else the name or question of its
    main idea, else its first sentence."""
    h = _headline(text, limit)
    return h[:1].upper() + h[1:]


def _headline(text: str, limit: int) -> str:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    for ln in lines:
        m = _TITLE.match(_MARKUP.sub("", ln))
        if m:
            return _plain(m.group(1))[:limit]
    for i, ln in enumerate(lines):
        if _MAIN_IDEA.search(ln) and len(ln) < 200:
            # "3. Main research idea: E-bikes as a friction isolator"
            name = _plain(ln.split(":", 1)[1]) if ":" in ln else ""
            if len(name) > 8:
                return name[:limit]
            # otherwise the question (or the first line) that follows the heading
            for nxt in lines[i + 1:i + 6]:
                m = _LABEL.match(_MARKUP.sub("", nxt).strip())
                if m:
                    return _plain(m.group(2))[:limit]
            if i + 1 < len(lines):
                return _plain(lines[i + 1])[:limit]
    for ln in lines:
        m = _LABEL.match(_MARKUP.sub("", ln).strip())
        if m:
            return _plain(m.group(2))[:limit]
    body = next((ln for ln in lines if not ln.startswith("#")), lines[0] if lines else "")
    return _plain(re.split(r"(?<=[.?!])\s", _plain(body), maxsplit=1)[0])[:limit]


def over_limit(p: FinalProposal, slack: float = 1.15) -> dict[str, int]:
    """Fields whose word count exceeds the limit (with a little slack)."""
    counts = {k: len(str(getattr(p, k) or "").split()) for k in WORD_LIMITS}
    return {k: n for k, n in counts.items() if n > WORD_LIMITS[k] * slack}


def response_citations(response: LLMResponse) -> list[str]:
    """URLs returned by the OpenRouter web plugin, if any."""
    try:
        anns = getattr(response.raw_response.choices[0].message, "annotations", None) or []
        out = []
        for a in anns:
            a = a if isinstance(a, dict) else a.model_dump()
            url = (a.get("url_citation") or {}).get("url")
            if url:
                out.append(url)
        return list(dict.fromkeys(out))
    except Exception:
        return []


def response_reasoning(response: LLMResponse) -> str:
    """Reasoning text returned with the completion (OpenRouter ``reasoning`` field), if any."""
    try:
        msg = response.raw_response.choices[0].message
        r = (getattr(msg, "model_extra", None) or {}).get("reasoning") or getattr(msg, "reasoning", None)
        return r if isinstance(r, str) else ""
    except Exception:
        return ""


def response_cost(response: LLMResponse) -> float:
    """OpenRouter-reported cost of a call, if available."""
    try:
        usage = response.raw_response.usage
        extra = getattr(usage, "model_extra", None) or {}
        return float(extra.get("cost") or getattr(usage, "cost", 0) or 0)
    except Exception:
        return 0.0


def is_pass(text: str) -> bool:
    return bool(_PASS.match(_HAND.sub("", text).strip()))


def raised_hand(text: str) -> bool:
    m = _HAND.search(text)
    return bool(m and m.group(1).lower() == "yes")


class RoundtableSeat(Agent):
    """A single seat; *seat_index* selects its model from the configured seat list."""

    role = AgentRole.ROUNDTABLE_SEAT
    pipeline_stage = PipelineStage.ROUNDTABLE_SEAT

    def __init__(
        self,
        router: LLMRouter,
        seat_index: int,
        agent_id: str | None = None,
        *,
        custom_system_prompt: str = "",
        web_search: bool = False,
        max_tokens: int = 16000,
    ) -> None:
        super().__init__(router, agent_id or f"p{seat_index + 1:02d}")
        self._seat_index = seat_index
        self._custom_system_prompt = custom_system_prompt
        self._web_search = web_search
        self._max_tokens = max_tokens
        self.cost = 0.0
        # the project's literature (aurora.literature.service.ProjectLiterature): with it the seat
        # searches the indexes before it presents or answers, and its references are checked
        self.literature: Any = None
        self.max_queries = 3
        self.results_per_query = 6
        # Called as ``on_progress(event, **data)``: ``ideation_stream`` while a turn is being written
        # (the text so far, every STREAM_INTERVAL s) and ``ideation_turn`` when it is done.
        self.on_progress: Callable[..., Awaitable[None]] | None = None

    @property
    def seat_index(self) -> int:
        return self._seat_index

    @property
    def model_id(self) -> str:
        try:
            _, model = self._router.get_provider_and_model(
                PipelineStage.ROUNDTABLE_SEAT, seat_index=self._seat_index
            )
            return model
        except Exception:
            return f"seat_{self._seat_index}_unknown"

    def system_prompt(self) -> str:
        base = self._custom_system_prompt or SEAT_SYSTEM_PROMPT
        papers = self.literature is not None
        if papers and "{papers_note}" not in base:
            base += "{papers_note}"
        return (base.replace("{seat_id}", self._agent_id)
                .replace("{search_note}", SEARCH_NOTE if self._web_search and "/" in self.model_id else "")
                .replace("{cite_how}", "cite papers by their key, see Papers below" if papers else "give the URL or DOI")
                .replace("{papers_note}", PAPERS_NOTE if papers else ""))

    async def _papers_for(self, parts: list[str], kind: str, round_number: int) -> str:
        """Ask the seat what to search for, search, and return the papers as a briefing section."""
        lit = self.literature
        what = "presentation" if kind == "presentation" else "answers to the remarks on your presentation"
        try:
            response = await self._router.generate(
                PipelineStage.ROUNDTABLE_SEAT,
                [Message.system(self.system_prompt()),
                 Message.user("\n\n".join(parts) + "\n\n## Before you write\n\n"
                              + QUERIES.format(what=what, n=self.max_queries))],
                seat_index=self._seat_index, temperature=0.3, max_tokens=2000,
            )
        except Exception:
            logger.warning("%s: choosing search queries failed", self._agent_id, exc_info=True)
            return ""
        self.cost += response_cost(response)
        queries = [q.strip(" \"'") for q in _QUERY.findall(response.content or "")][: self.max_queries]
        if not queries:
            return ""
        context = (f"Ideation · {self._agent_id}'s presentation" if kind == "presentation"
                   else f"Ideation · {self._agent_id}'s answers (round {round_number})")
        cards: list[str] = []
        seen: set[str] = set()
        for q in queries:
            try:
                found = await lit.search(q, by=self._agent_id, context=context, limit=self.results_per_query)
            except Exception:
                logger.warning("%s: literature search failed", self._agent_id, exc_info=True)
                continue
            fresh = [e for e in found if e["paper_id"] not in seen]
            seen.update(e["paper_id"] for e in fresh)
            if fresh:
                from aurora.literature.service import paper_card
                cards.append(f"Search: “{q}”\n" + "\n".join(paper_card(e) for e in fresh))
            else:
                cards.append(f"Search: “{q}”\n(nothing found)")
        return ("## Papers found for your searches\n\nCite them by key, e.g. [" +
                next((c.split("[", 1)[1].split("]", 1)[0] for c in cards if "[" in c), "Key00") + "]. "
                "These are real papers from the indexes; abstracts are shortened.\n\n" + "\n\n".join(cards))

    async def _check_references(self, content: str, kind: str, round_number: int, agenda_item: str) -> str:
        """Check the references in a turn; the note (if any) is added to the turn for everyone to see."""
        place = {"presentation": "presentation", "response": f"answers, round {round_number}",
                 "remarks": f"remarks on {agenda_item or 'an item'}", "open": "open discussion"}.get(kind, kind)
        try:
            cites = await self.literature.check_text(content, by=self._agent_id,
                                                     place=f"Ideation ({self._agent_id}, {place})")
        except Exception:
            logger.warning("%s: checking references failed", self._agent_id, exc_info=True)
            return ""
        return cites.note() if not cites.clean else ""

    def briefing(self, context: AgentContext, topic: str) -> str:
        return f"## Briefing\n\n{self._format_context(context)}\n\n## Topic\n\n{topic}"

    async def _turn(self, context: AgentContext, topic: str, transcript: str, instructions: str,
                    *, kind: str, round_number: int, agenda_item: str = "") -> SeatContribution:
        parts = [self.briefing(context, topic)]
        if transcript:
            parts.append(f"## Meeting transcript so far\n\n{transcript}")
        if self.literature is not None and kind in SEARCH_TURNS:
            papers = await self._papers_for(parts, kind, round_number)
            if papers:
                parts.append(papers)
        parts.append(f"## Your turn (you are {self._agent_id})\n\n{instructions}")
        # the web plugin is OpenRouter's (vendor/model IDs); other providers search nothing
        searchable = "/" in self.model_id
        kwargs = {"extra_body": {"plugins": [{"id": "web", "engine": "native", "max_results": 5}]}} \
            if self._web_search and searchable and kind in SEARCH_TURNS else {}
        turn = {"seat": self._agent_id, "model": self.model_id, "kind": kind, "item": agenda_item,
                 "round": round_number}
        live = _LiveTurn(self.on_progress, turn) if self.on_progress else None
        if live:
            await live.start()
        try:
            response = await self._router.generate(
                PipelineStage.ROUNDTABLE_SEAT,
                [Message.system(self.system_prompt()), Message.user("\n\n".join(parts))],
                seat_index=self._seat_index,
                temperature=0.7,
                max_tokens=self._max_tokens,
                on_delta=live.add if live else None,
                **kwargs,
            )
        except Exception as exc:
            if live:
                await live.fail(exc)
            raise
        self.cost += response_cost(response)
        content = response.content.strip()
        if self.literature is not None and content and not is_pass(content):
            note = await self._check_references(content, kind, round_number, agenda_item)
            if note:
                content = f"{content}\n\n{note}"
        if self.on_progress:
            try:
                await self.on_progress("ideation_turn", **turn, content=content,
                                       citations=len(response_citations(response)),
                                       headline=headline(content) if kind == "presentation" else "")
            except Exception:
                logger.debug("ideation_turn callback failed", exc_info=True)
        return SeatContribution(
            seat_id=self._agent_id,
            model_id=response.model_id or self.model_id,
            round_number=round_number,
            content=content,
            unverified_claims=[m.strip() for m in _UNVERIFIED.findall(content)],
            kind=kind,
            agenda_item=agenda_item,
            citations=response_citations(response),
            reasoning=response_reasoning(response),
        )

    async def prepare(self, context: AgentContext, topic: str) -> SeatContribution:
        return await self._turn(context, topic, "", PREPARE, kind="presentation", round_number=0)

    async def remark(self, context: AgentContext, topic: str, transcript: str, presenter: str,
                     round_number: int) -> SeatContribution:
        followup = " (follow-up, after the presenter's answers)" if round_number > 1 else ""
        return await self._turn(context, topic, transcript,
                                REMARKS.format(presenter=presenter, followup=followup),
                                kind="remarks", round_number=round_number, agenda_item=presenter)

    async def self_remark(self, context: AgentContext, topic: str, transcript: str) -> SeatContribution:
        return await self._turn(context, topic, transcript, SELF_REMARKS, kind="remarks",
                                round_number=1, agenda_item=self._agent_id)

    async def respond(self, context: AgentContext, topic: str, transcript: str,
                      round_number: int) -> SeatContribution:
        return await self._turn(context, topic, transcript, RESPOND, kind="response",
                                round_number=round_number, agenda_item=self._agent_id)

    async def open_discussion(self, context: AgentContext, topic: str, transcript: str,
                              round_number: int) -> SeatContribution:
        return await self._turn(context, topic, transcript, OPEN if round_number == 1 else OPEN_REPLY,
                                kind="open", round_number=round_number)

    async def write_proposal(self, context: AgentContext, topic: str, transcript: str,
                             decision: str | None) -> FinalProposal:
        instructions = WRITE.format(decision=decision) if decision else WRITE_ALONE
        messages = [
            Message.system(self.system_prompt()),
            Message.user(f"{self.briefing(context, topic)}\n\n## Meeting transcript\n\n{transcript}\n\n"
                         f"## Your task (you are {self._agent_id})\n\n{instructions}"),
        ]
        for attempt in range(3):  # providers occasionally cut a structured response short
            try:
                proposal = await self._router.generate_structured(
                    PipelineStage.ROUNDTABLE_SEAT, messages, FinalProposal,
                    seat_index=self._seat_index, temperature=0.3, max_tokens=16000,
                )
                break
            except Exception as exc:
                if attempt == 2:
                    raise
                logger.info("Seat %s proposal writing failed, retrying: %s", self._agent_id, exc)
        for _ in range(2):
            over = over_limit(proposal)
            if not over:
                break
            shorten = Message.user(
                "Shorten this proposal to the word limits. Fields over the limit: "
                + ", ".join(f"{k} ({n} words, limit {WORD_LIMITS[k]})" for k, n in over.items())
                + ". Keep the substance; cut repetition and detail first.\n\n"
                + json.dumps(proposal.model_dump(), ensure_ascii=False, indent=1))
            proposal = await self._router.generate_structured(
                PipelineStage.ROUNDTABLE_SEAT, [Message.system(self.system_prompt()), shorten], FinalProposal,
                seat_index=self._seat_index, temperature=0.0, max_tokens=16000,
            )
        return proposal

    async def run(self, context: AgentContext) -> list[AgentAction]:
        """Agent interface: prepare a presentation."""
        try:
            contribution = await self.prepare(context, context.extra.get("topic", context.current_goal))
        except Exception as exc:
            logger.warning("Seat %s failed: %s", self._agent_id, exc)
            return [self._make_action(AgentActionType.LOG_MESSAGE,
                                      {"level": "warning", "message": f"Seat {self._agent_id} failed: {exc}"})]
        return [self._make_action(AgentActionType.SUBMIT_CONTRIBUTION,
                                  {"contribution": contribution.model_dump()},
                                  reasoning=contribution.content[:200])]
