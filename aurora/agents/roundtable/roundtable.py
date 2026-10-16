"""Roundtable orchestrator — a research group meeting among heterogeneous model seats (Section 2.4).

Protocol:
  Preparation   — every seat looks into the literature and prepares a presentation with one main idea
  Agenda items  — one per presentation, in random order: the other seats make remarks (questions,
                  concerns, suggestions, evidence, build-ons, reasoned agreement) or pass; the presenter
                  responds; seats that raised a hand may follow up; up to ``roundtable_item_rounds``
                  exchanges. The chair records the threads and their outcomes after each item.
  Open discussion — cross-cutting remarks and replies.
  Decision      — the chair decides the direction from the notes and transcript (no ranking or voting
                  by seats); the chosen presenter writes the final proposal.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import re
import uuid
from datetime import datetime

from aurora.agents.base import AgentContext
from aurora.agents.roundtable.chair import Decision, ItemNotes, RoundtableChair
from aurora.agents.roundtable.seat import RoundtableSeat, is_pass, raised_hand
from aurora.config import AuroraConfig
from aurora.llm.router import LLMRouter
from aurora.models.roundtable import RoundtableProposal, SeatContribution

logger = logging.getLogger(__name__)


class Roundtable:
    """Runs the group meeting and returns the proposal for the chosen direction."""

    def __init__(self, config: AuroraConfig, router: LLMRouter) -> None:
        self._config = config
        self._router = router
        self._contributions: list[SeatContribution] = []
        self.notes: list[ItemNotes] = []
        self.decision: Decision | None = None

        prompts = ([sc.prompt or config.roundtable_seat_prompt for sc in config.roundtable_seats]
                   or [config.roundtable_seat_prompt] * config.num_roundtable_seats)
        self._seats = [
            RoundtableSeat(router, seat_index=i, custom_system_prompt=p, web_search=config.roundtable_web_search)
            for i, p in enumerate(prompts)
        ]
        self._chair = RoundtableChair(router, custom_system_prompt=config.roundtable_system_prompt)

    @property
    def seat_ids(self) -> list[str]:
        return [s.agent_id for s in self._seats]

    @property
    def contributions(self) -> list[SeatContribution]:
        return list(self._contributions)

    @property
    def seat_cost(self) -> float:
        return sum(s.cost for s in self._seats)

    # -- public interface ------------------------------------------------------

    async def deliberate(self, context: AgentContext, topic: str | None = None) -> RoundtableProposal:
        topic = topic or context.current_goal
        self._contributions, self.notes, self.decision = [], [], None
        rnd = random.Random(int(hashlib.md5(topic.encode()).hexdigest(), 16))
        by_id = {s.agent_id: s for s in self._seats}

        await self._parallel([s.prepare(context, topic) for s in self._seats], rnd)

        agenda = self.seat_ids[:]
        rnd.shuffle(agenda)
        for presenter in agenda:
            start = len(self._contributions)
            speakers = [s for s in self._seats if s.agent_id != presenter]
            for k in range(1, self._config.roundtable_item_rounds + 1):
                remarks = await self._parallel(
                    [s.remark(context, topic, self.transcript(), presenter, k) for s in speakers], rnd)
                spoken = [r for r in remarks if not is_pass(r.content)]
                if not spoken:
                    break
                await self._parallel([by_id[presenter].respond(context, topic, self.transcript(), k)], rnd)
                speakers = [by_id[r.seat_id] for r in spoken if raised_hand(r.content)]
                if not speakers:
                    break
            self.notes.append(await self._chair.notes(presenter, self.transcript(self._contributions[start:])))

        start = len(self._contributions)
        for k in range(1, self._config.roundtable_item_rounds + 1):
            remarks = await self._parallel(
                [s.open_discussion(context, topic, self.transcript(), k) for s in self._seats], rnd)
            if all(is_pass(r.content) for r in remarks):
                break
        self.notes.append(await self._chair.notes("open discussion", self.transcript(self._contributions[start:])))

        briefing = self._seats[0].briefing(context, topic)
        try:
            self.decision = await self._chair.decide(briefing, self.notes, self.transcript())
            # the chair sometimes writes a sentence instead of a bare seat ID
            named = [x for x in re.findall(r"p\d{2}", self.decision.chosen) if x in by_id]
            writer = by_id[named[0]] if named else self._seats[0]
            decision_txt = json.dumps(self.decision.model_dump(), ensure_ascii=False, indent=1)
            final = await writer.write_proposal(context, topic, self.transcript(), decision_txt)
        except Exception as exc:
            logger.error("Roundtable decision/writing failed: %s", exc)
            return self._build_fallback_proposal(topic)
        return RoundtableProposal(
            proposal_id=f"prop_{uuid.uuid4().hex[:8]}",
            title=final.title or topic[:100],
            description=final.hypothesis,
            research_questions=[final.problem] if final.problem else [],
            proposed_methods=[final.method] if final.method else [],
            expected_outcomes=final.expected_outcomes,
            hypothesis=final.hypothesis,
            alternatives_considered=final.alternatives_considered,
            falsification_check=final.falsification_check,
            unresolved_disagreements=final.unresolved_disagreements,
            meeting_notes=[n.model_dump() for n in self.notes],
            decision=self.decision.model_dump(),
            contributions=list(self._contributions),
            created_at=datetime.utcnow(),
        )

    def transcript(self, contributions: list[SeatContribution] | None = None) -> str:
        """Meeting transcript in order of speaking; nothing is truncated."""
        label = {"presentation": "Presentation", "remarks": "Remarks on {item}", "response": "Response",
                 "open": "Open discussion"}
        blocks = []
        for c in self._contributions if contributions is None else contributions:
            if c.kind == "remarks" and is_pass(c.content):
                continue
            head = label.get(c.kind, c.kind).format(item=c.agenda_item)
            blocks.append(f"### {c.seat_id} — {head}\n\n{c.content}")
        return "\n\n".join(blocks)

    # -- helpers ---------------------------------------------------------------

    async def _parallel(self, coros: list, rnd: random.Random) -> list[SeatContribution]:
        """Run one round of turns in parallel; add them to the transcript in random order."""
        results = await asyncio.gather(*coros, return_exceptions=True)
        ok = []
        for res in results:
            if isinstance(res, Exception):
                logger.warning("Roundtable turn failed: %s", res)
                continue
            ok.append(res)
        rnd.shuffle(ok)
        self._contributions.extend(ok)
        return ok

    def _build_fallback_proposal(self, topic: str) -> RoundtableProposal:
        return RoundtableProposal(
            proposal_id=f"prop_{uuid.uuid4().hex[:8]}",
            title=f"Research Direction: {topic[:80]}",
            description="Auto-generated from roundtable discussion (decision or writing failed)",
            research_questions=[topic],
            meeting_notes=[n.model_dump() for n in self.notes],
            contributions=list(self._contributions),
            created_at=datetime.utcnow(),
        )
