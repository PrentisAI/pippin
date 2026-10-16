"""Roundtable chair — keeps the meeting notes and decides the direction (Section 2.4).

The chair does not argue positions. After each agenda item it records the discussion threads
(questions, concerns, suggestions) and what became of them; at the end it decides, like a PI who
has listened to everyone, which direction the group pursues, and records why the others were not
chosen and which concerns remain open.
"""

from __future__ import annotations

import json
import logging

from pydantic import BaseModel, Field

from aurora.agents.base import (
    Agent,
    AgentAction,
    AgentActionType,
    AgentContext,
    AgentRole,
)
from aurora.config import PipelineStage
from aurora.llm.base import Message
from aurora.llm.router import LLMRouter

logger = logging.getLogger(__name__)


CHAIR_SYSTEM_PROMPT = """\
You chair a research group meeting. You do not argue positions yourself. You keep the meeting notes, \
and at the end you decide the direction the group pursues, the way a PI does after listening to \
everyone. Be faithful to what was actually said."""

NOTES_PROMPT = """\
Record the notes for the agenda item just discussed ({item}). List the discussion threads (at most 12, \
merging minor ones): each question, concern, suggestion, piece of evidence, or idea someone raised, who \
raised it, and what became of it by the end of the item. Attribute each thread to the seat whose \
heading it appears under in the transcript, not to whoever later adopted or repeated it. A thread is \
"answered" only if the reply engaged with its substance (with evidence, a rebuttal, or a concrete change); \
a reply that merely agrees without addressing it leaves the thread "open". Keep each gist and note to one \
short sentence.

## Agenda item transcript
{transcript}"""

DECIDE_PROMPT = """\
The meeting is over. Decide which direction the group will pursue (with the modifications the meeting \
accepted). Judge the directions, not the presenters, by:
- what we would learn if it works that we do not know now (new knowledge, not re-verifying a published \
result or only preparing groundwork);
- whether the important concerns about it were resolved, by a convincing rebuttal or by a change to the \
design;
- how well it is supported by evidence, and whether it can be done in the setting described;
- how directly it addresses the topic.
Guard against these biases: do not reward length, confidence, or polish; do not count how many remarks a \
presenter accepted (accepting a mistaken objection is worse than rebutting it, and a plan that grew in \
scope to absorb every suggestion is weaker, not stronger); do not favour a direction for the order in \
which it was presented. Credit each idea to the seat that raised it. Do not merge separate directions \
into one plan. Fill in every field: list the accepted changes, give a reason for each direction not \
chosen, and record the concerns that remain open and ideas worth keeping for later.

## Briefing
{briefing}

## Meeting notes
{notes}

## Full transcript
{transcript}"""


SELECT_PROMPT = """\
Several researchers each prepared, independently, one research idea on the topic below; they did not \
discuss them. Decide which idea the group should pursue. Judge the ideas, not how they are written:
- what we would learn if it works that we do not know now (new knowledge, not re-verifying a published \
result or only preparing groundwork);
- how well it is supported by evidence, and whether it can be done in the setting described;
- whether its design guards against misleading results (controls, confounds, circularity);
- how directly it addresses the topic.
Do not reward length, confidence, or polish, and ignore the order in which the ideas are listed. Do not \
merge ideas. Give a reason for each idea not chosen.

## Briefing
{briefing}

## Ideas
{ideas}"""


class Selection(BaseModel):
    chosen: str = Field(description="Label of the chosen idea, e.g. 'B'")
    rationale: str = Field(description="Why this idea, two or three sentences")
    not_chosen: list[str] = Field(description="'<label>: reason' for each other idea")


class Thread(BaseModel):
    raised_by: str = Field(description="Seat ID")
    kind: str = Field(description="question | concern | suggestion | evidence | idea | agreement")
    about: str = Field(default="", description="Seat ID whose work it concerns, or 'all'")
    gist: str = Field(description="One sentence")
    outcome: str = Field(description="accepted | answered | rejected | open | deferred")
    outcome_note: str = Field(default="", description="One sentence on how it was resolved, if at all")


class ItemNotes(BaseModel):
    item: str = ""
    threads: list[Thread] = Field(default_factory=list)
    summary: str = Field(default="", description="Two or three sentences")


class Decision(BaseModel):
    chosen: str = Field(description="Seat ID whose direction the group pursues")
    direction: str = Field(description="One or two sentences describing the direction as modified by the meeting")
    rationale: str = Field(description="Why this direction, based on the discussion")
    # no defaults: the fields must be present in the JSON schema's "required" list, otherwise models omit them
    accepted_changes: list[str] = Field(description="Changes the meeting agreed for it")
    not_chosen: list[str] = Field(description="'<seat>: reason' for each other direction")
    open_concerns: list[str] = Field(description="Concerns that remain open")
    backlog: list[str] = Field(description="Ideas worth pursuing later")


class RoundtableChair(Agent):
    """Chair of the group meeting: keeps notes and decides the direction."""

    role = AgentRole.ROUNDTABLE_CHAIR
    pipeline_stage = PipelineStage.ROUNDTABLE_CHAIR

    def __init__(
        self, router: LLMRouter, agent_id: str | None = None,
        *, custom_system_prompt: str = "",
    ) -> None:
        super().__init__(router, agent_id or "chair")
        self._custom_system_prompt = custom_system_prompt

    def system_prompt(self) -> str:
        return self._custom_system_prompt or CHAIR_SYSTEM_PROMPT

    async def notes(self, item: str, transcript: str) -> ItemNotes:
        messages = [Message.system(self.system_prompt()),
                    Message.user(NOTES_PROMPT.format(item=item, transcript=transcript))]
        try:
            n = await self._structured_retry(messages, ItemNotes)
            n.item = item
            return n
        except Exception as exc:
            logger.warning("Chair notes failed for %s: %s", item, exc)
            return ItemNotes(item=item, summary=f"(notes failed: {exc})")

    async def _structured_retry(self, messages: list[Message], schema, attempts: int = 2):
        """Structured call with a generous token budget (reasoning chairs spend much of it) and one retry."""
        for k in range(attempts):
            try:
                return await self._generate_structured(messages, schema, temperature=0.0, max_tokens=32000)
            except Exception as exc:
                if k == attempts - 1:
                    raise
                logger.info("Chair %s call failed, retrying: %s", schema.__name__, exc)

    async def decide(self, briefing: str, notes: list[ItemNotes], transcript: str) -> Decision:
        notes_txt = "\n\n".join(json.dumps(n.model_dump(), ensure_ascii=False, indent=1) for n in notes)
        messages = [Message.system(self.system_prompt()),
                    Message.user(DECIDE_PROMPT.format(briefing=briefing, notes=notes_txt, transcript=transcript))]
        return await self._structured_retry(messages, Decision)

    async def select(self, briefing: str, ideas: dict[str, str]) -> Selection:
        """Pick one of several independently prepared ideas (no meeting); *ideas* maps label -> text."""
        txt = "\n\n".join(f"### Idea {k}\n\n{v}" for k, v in ideas.items())
        messages = [Message.system(self.system_prompt()),
                    Message.user(SELECT_PROMPT.format(briefing=briefing, ideas=txt))]
        return await self._structured_retry(messages, Selection)

    async def run(self, context: AgentContext) -> list[AgentAction]:
        """Agent interface: write notes for ``context.extra['transcript']``."""
        n = await self.notes(context.extra.get("item", "meeting"), context.extra.get("transcript", ""))
        return [self._make_action(AgentActionType.LOG_MESSAGE, {"notes": n.model_dump()}, reasoning=n.summary)]
