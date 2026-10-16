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
actually found (give the URL or DOI). Anything beyond these must be flagged '(my claim, unverified)'. \
Keep what sources say separate from your own reasoning.{search_note}

You're welcome to bring relevant knowledge, methods and ideas from adjacent fields---or even distant \
but structurally related ones---to bear on this question."""

SEARCH_NOTE = (" When preparing and when answering remarks on your own work you can search the web; use it to "
               "check what is already known and to verify claims.")
SEARCH_TURNS = {"presentation", "response"}

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
        return base.replace("{seat_id}", self._agent_id).replace(
            "{search_note}", SEARCH_NOTE if self._web_search else ""
        )

    def briefing(self, context: AgentContext, topic: str) -> str:
        return f"## Briefing\n\n{self._format_context(context)}\n\n## Topic\n\n{topic}"

    async def _turn(self, context: AgentContext, topic: str, transcript: str, instructions: str,
                    *, kind: str, round_number: int, agenda_item: str = "") -> SeatContribution:
        parts = [self.briefing(context, topic)]
        if transcript:
            parts.append(f"## Meeting transcript so far\n\n{transcript}")
        parts.append(f"## Your turn (you are {self._agent_id})\n\n{instructions}")
        kwargs = {"extra_body": {"plugins": [{"id": "web", "engine": "native", "max_results": 5}]}} \
            if self._web_search and kind in SEARCH_TURNS else {}
        response = await self._router.generate(
            PipelineStage.ROUNDTABLE_SEAT,
            [Message.system(self.system_prompt()), Message.user("\n\n".join(parts))],
            seat_index=self._seat_index,
            temperature=0.7,
            max_tokens=self._max_tokens,
            **kwargs,
        )
        self.cost += response_cost(response)
        content = response.content.strip()
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
