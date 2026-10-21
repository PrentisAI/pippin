"""Multi-agent ideation among heterogeneous model seats (Section 3.4 of the paper).

Two configurations (``ideation.mode``):

* ``roundtable`` — a research group meeting, described below.
* ``independent`` — every seat prepares one idea on its own; the chair picks
  one without any discussion and its author writes the proposal.  Cheaper,
  and in the paper's evaluation the chair's picks were at least as good.

Roundtable protocol:
  Preparation   — every seat looks into the literature and prepares a presentation with one main idea
  Agenda items  — one per presentation, in random order: the other seats make remarks (questions,
                  concerns, suggestions, evidence, build-ons, reasoned agreement) or pass; the presenter
                  responds; seats that raised a hand may follow up; up to ``ideation.rounds_per_item``
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
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path

from aurora.agents.base import AgentContext
from aurora.agents.roundtable.chair import Decision, ItemNotes, RoundtableChair, Selection
from aurora.agents.roundtable.seat import RoundtableSeat, headline, is_pass, raised_hand
from aurora.config import AuroraConfig
from aurora.llm.router import LLMRouter
from aurora.models.roundtable import RoundtableProposal, SeatContribution

logger = logging.getLogger(__name__)


Progress = Callable[..., Awaitable[None]]


class Roundtable:
    """Runs the group meeting and returns the proposal for the chosen direction."""

    def __init__(self, config: AuroraConfig, router: LLMRouter, progress: Progress | None = None,
                 state_path: Path | None = None, literature=None) -> None:
        self._progress = progress
        self._state_path = Path(state_path) if state_path else None
        self._state: dict = {}
        self._config = config
        self._router = router
        self._contributions: list[SeatContribution] = []
        self.notes: list[ItemNotes] = []
        self.decision: Decision | None = None

        ideation = config.ideation
        prompts = ([sc.prompt or ideation.seat_prompt for sc in ideation.seats]
                   or [ideation.seat_prompt] * len(config.models.ideation_seats))
        self._seats = [
            RoundtableSeat(router, seat_index=i, custom_system_prompt=p, web_search=ideation.web_search)
            for i, p in enumerate(prompts)
        ]
        for seat in self._seats:
            seat.on_progress = self._seat_progress if progress is not None else None
            # the project's literature: seats search the indexes and their references are checked
            seat.literature = literature
            seat.max_queries = config.literature.queries_per_turn
            seat.results_per_query = config.literature.results_per_query
        self._chair = RoundtableChair(router, custom_system_prompt=ideation.chair_prompt)

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

    async def run(self, context: AgentContext, topic: str | None = None) -> RoundtableProposal:
        """Run the configured ideation mode and return the proposal for the chosen direction."""
        if self._config.ideation.mode == "independent":
            return await self.select_independently(context, topic)
        return await self.deliberate(context, topic)

    async def select_independently(self, context: AgentContext, topic: str | None = None) -> RoundtableProposal:
        topic = topic or context.current_goal
        state = self._load_state(topic)
        rnd = random.Random(int(hashlib.md5(topic.encode()).hexdigest(), 16))
        if state.get("prepared"):
            drafts = list(self._contributions)
            await self._emit("resumed", prepared=len(drafts))
        else:
            drafts = await self._prepare_all(context, topic, rnd)
            self._save_state(topic, prepared=True)
        # anonymous, shuffled labels so the chair cannot favour a model or a position
        order = drafts[:]
        rnd.shuffle(order)
        labels = {chr(ord("A") + k): c for k, c in enumerate(order)}
        briefing = self._seats[0].briefing(context, topic)
        if state.get("selection"):
            selection = Selection(**state["selection"])
        else:
            await self._emit("chair_selecting", candidates=len(labels))
            selection = await self._chair.select(briefing, {lab: c.content for lab, c in labels.items()})
            self._save_state(topic, selection=selection.model_dump())
        chosen = next((labels[x] for x in re.findall(r"[A-Z]", selection.chosen) if x in labels), order[0])
        await self._emit("decided", chosen=chosen.seat_id, headline=headline(chosen.content),
                         rationale=selection.rationale)
        writer = next(s for s in self._seats if s.agent_id == chosen.seat_id)
        final = await writer.write_proposal(
            context, topic, f"### {chosen.seat_id} — presentation\n\n{chosen.content}", None)
        self.decision = None
        self._finish_state()
        return self._proposal(final, topic, decision={
            "mode": "independent", "chosen": chosen.seat_id,
            "labels": {lab: c.seat_id for lab, c in labels.items()}, **selection.model_dump()})

    async def deliberate(self, context: AgentContext, topic: str | None = None) -> RoundtableProposal:
        topic = topic or context.current_goal
        state = self._load_state(topic)
        rnd = random.Random(int(hashlib.md5(topic.encode()).hexdigest(), 16))
        by_id = {s.agent_id: s for s in self._seats}

        if state.get("prepared"):
            await self._emit("resumed", prepared=sum(c.kind == "presentation" for c in self._contributions),
                             items_done=len(state.get("done_items", [])))
        else:
            await self._prepare_all(context, topic, rnd)
            self._save_state(topic, prepared=True)

        agenda = state.get("agenda")
        if not agenda:
            agenda = self.seat_ids[:]
            rnd.shuffle(agenda)
            self._save_state(topic, agenda=agenda)
        done_items: list[str] = list(state.get("done_items", []))
        for n, presenter in enumerate(agenda, 1):
            if presenter in done_items:
                continue
            await self._emit("agenda_item", item=n, of=len(agenda), presenter=presenter)
            start = len(self._contributions)
            speakers = [s for s in self._seats if s.agent_id != presenter]
            for k in range(1, self._config.ideation.rounds_per_item + 1):
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
            done_items.append(presenter)
            self._save_state(topic, done_items=done_items)
            await self._emit("item_notes", presenter=presenter, summary=self.notes[-1].summary,
                             threads=len(self.notes[-1].threads))

        if not state.get("open_done"):
            await self._emit("open_discussion")
            start = len(self._contributions)
            for k in range(1, self._config.ideation.rounds_per_item + 1):
                remarks = await self._parallel(
                    [s.open_discussion(context, topic, self.transcript(), k) for s in self._seats], rnd)
                if all(is_pass(r.content) for r in remarks):
                    break
            self.notes.append(await self._chair.notes("open discussion",
                                                      self.transcript(self._contributions[start:])))
            self._save_state(topic, open_done=True)

        briefing = self._seats[0].briefing(context, topic)
        if state.get("decision"):
            self.decision = Decision(**state["decision"])
        else:
            await self._emit("chair_deciding")
            try:
                self.decision = await self._chair.decide(briefing, self.notes, self.transcript())
            except Exception as exc:
                raise RuntimeError(f"Ideation chair ({self._config.models.ideation_chair}) could not decide: "
                                   f"{exc}") from exc
            self._save_state(topic, decision=self.decision.model_dump())
        # the chair sometimes writes a sentence instead of a bare seat ID
        named = [x for x in re.findall(r"p\d{2}", self.decision.chosen) if x in by_id]
        writer = by_id[named[0]] if named else self._seats[0]
        await self._emit("decided", chosen=writer.agent_id, headline=self.decision.direction,
                         rationale=self.decision.rationale)
        decision_txt = json.dumps(self.decision.model_dump(), ensure_ascii=False, indent=1)
        final = await writer.write_proposal(context, topic, self.transcript(), decision_txt)
        self._finish_state()
        return self._proposal(final, topic, decision=self.decision.model_dump())

    # -- saving the meeting as it goes (so a stopped campaign resumes mid-meeting) --

    def _load_state(self, topic: str) -> dict:
        """Restore the meeting saved for *topic*, if any; otherwise start empty."""
        self._contributions, self.notes, self.decision = [], [], None
        self._state: dict = {"topic": topic, "mode": self._config.ideation.mode,
                             "seats": self._config.models.ideation_seats}
        path = self._state_path
        if path is None or not path.exists():
            return {}
        try:
            saved = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return {}
        if (saved.get("topic"), saved.get("mode"), saved.get("seats")) != (
                topic, self._config.ideation.mode, self._config.models.ideation_seats):
            return {}       # a different meeting
        self._state = saved
        self._contributions = [SeatContribution(**c) for c in saved.get("contributions", [])]
        self.notes = [ItemNotes(**n) for n in saved.get("notes", [])]
        logger.info("Resuming the ideation meeting from %s", path)
        return saved

    def _save_state(self, topic: str, **updates) -> None:
        if self._state_path is None:
            return
        self._state.update(updates)
        self._state["contributions"] = [c.model_dump(mode="json") for c in self._contributions]
        self._state["notes"] = [n.model_dump(mode="json") for n in self.notes]
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._state, ensure_ascii=False))
        tmp.replace(self._state_path)

    def _finish_state(self) -> None:
        """The meeting is over: keep its record, but never resume from it."""
        if self._state_path is not None and self._state_path.exists():
            self._state_path.replace(self._state_path.with_name("meeting_state.finished.json"))

    def _proposal(self, final, topic: str, decision: dict) -> RoundtableProposal:
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
            decision=decision,
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

    async def _emit(self, step: str, **data) -> None:
        """Report meeting progress (``ideation_progress`` events) to whoever listens."""
        if self._progress is not None:
            try:
                await self._progress("ideation_progress", step=step, **data)
            except Exception:
                logger.debug("ideation progress callback failed", exc_info=True)

    async def _seat_progress(self, event: str, **data) -> None:
        """Pass a seat's live turn (``ideation_stream``) and finished turn (``ideation_turn``) on."""
        if self._progress is not None:
            try:
                await self._progress(event, **data)
            except Exception:
                logger.debug("ideation %s callback failed", event, exc_info=True)

    async def _prepare_all(self, context: AgentContext, topic: str, rnd: random.Random) -> list[SeatContribution]:
        """Every seat prepares its idea; fail loudly when too few seats could take part."""
        await self._emit("preparing", seats=[{"seat": s.agent_id, "model": s.model_id} for s in self._seats])
        drafts = await self._parallel([s.prepare(context, topic) for s in self._seats], rnd)
        await self._emit("prepared", ideas=[{"seat": d.seat_id, "model": d.model_id,
                                             "headline": headline(d.content)} for d in drafts],
                         failed=len(self._seats) - len(drafts))
        if not drafts:
            raise RuntimeError(
                "Every ideation seat failed to prepare an idea; check the API key and the model IDs "
                f"in models.ideation_seats ({[s.model_id for s in self._seats]}). See the log for the errors.")
        if len(drafts) < len(self._seats):
            logger.warning("Only %d of %d ideation seats prepared an idea", len(drafts), len(self._seats))
        return drafts

    async def _parallel(self, coros: list, rnd: random.Random) -> list[SeatContribution]:
        """Run one round of turns in parallel; add them to the transcript in random order."""
        results = await asyncio.gather(*coros, return_exceptions=True)
        ok = []
        for res in results:
            if isinstance(res, Exception):
                logger.error("Ideation turn failed: %s", res)
                continue
            ok.append(res)
        rnd.shuffle(ok)
        self._contributions.extend(ok)
        return ok
