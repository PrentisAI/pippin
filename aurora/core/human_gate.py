"""Human gates: the points where the campaign waits for a human decision.

A gate opens when the milestone auditor escalates (or a phase keeps failing its
audit), when the live auditor escalates during research, when screening keeps
rejecting proposals, and, if configured, before the campaign finishes.

Every gate is answered with one of these actions:

* ``continue`` — go on as planned (a message, if any, is passed on as a note);
* ``instruct`` — go on, following the human's message;
* ``propose`` — (only where a gate offers it, e.g. screening) the human's message says which
  direction to pursue: one of the proposals, with or without changes, or their own idea.  The
  decision may ask for the direction to go through ``ideation`` and/or ``screening`` again
  (``rerun``); by default it goes straight on;
* ``stop`` — end the campaign.

Anyone may answer: the web console (approval bar), the front desk agent (when
the human replies in chat), or the terminal for ``aurora run`` in an
interactive shell.  In ``autonomous`` mode, or when nobody answers within
``human.gate_timeout_minutes``, the gate resolves by itself and says so in the
event log.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal

from aurora.config import AutonomyMode, HumanConfig

logger = logging.getLogger(__name__)

Action = Literal["continue", "instruct", "propose", "stop"]
ACTIONS: tuple[str, ...] = ("continue", "instruct", "stop")     # offered by every gate
NEEDS_MESSAGE = {"instruct", "propose"}
RERUN_PHASES = ("ideation", "screening")    # what a chosen direction may go through again


@dataclass
class GateRequest:
    gate_id: str
    source: str            # milestone_audit | supervisor | screening | termination
    title: str             # one line, shown as the headline
    detail: str            # what happened and why a decision is needed
    options: list[str] = field(default_factory=lambda: list(ACTIONS))
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class GateDecision:
    action: Action
    message: str = ""
    decided_by: str = "human"   # human | frontdesk | autonomous | timeout | system | auditor
    gate_id: str = ""
    rerun: list[str] = field(default_factory=list)   # for propose: phases the direction goes through

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


Publish = Callable[..., Awaitable[None]]


class HumanGate:
    """Opens gates, waits for their decisions and records them."""

    def __init__(self, config: HumanConfig, publish: Publish, log_path: Path | None = None) -> None:
        self._config = config
        self._publish = publish
        self._log_path = log_path
        self.autonomy: AutonomyMode = config.autonomy
        self._pending: dict[str, tuple[GateRequest, asyncio.Future[GateDecision]]] = {}
        self._console_task: asyncio.Task | None = None
        self.interactive_console = False

    # -- state --------------------------------------------------------------

    @property
    def pending(self) -> list[GateRequest]:
        return [req for req, _ in self._pending.values()]

    def set_autonomy(self, mode: AutonomyMode | str) -> None:
        """Switch mode; switching to autonomous lets every waiting gate continue."""
        self.autonomy = AutonomyMode(mode)
        if self.autonomy == AutonomyMode.AUTONOMOUS:
            for gate_id in list(self._pending):
                self.resolve(gate_id, "continue", "switched to autonomous mode", decided_by="autonomous")

    # -- opening and answering gates -----------------------------------------

    async def request(self, source: str, title: str, detail: str,
                      options: list[str] | None = None) -> GateDecision:
        """Open a gate and wait for its decision (*options*: the actions it offers)."""
        req = GateRequest(gate_id=f"gate_{uuid.uuid4().hex[:8]}", source=source, title=title, detail=detail,
                          options=list(options or ACTIONS))
        await self._publish("human_gate_opened", **req.to_dict())
        logger.warning("Human gate %s (%s): %s", req.gate_id, source, title)

        if self.autonomy == AutonomyMode.AUTONOMOUS:
            decision = GateDecision("continue", "autonomous mode", decided_by="autonomous", gate_id=req.gate_id)
        else:
            future: asyncio.Future[GateDecision] = asyncio.get_running_loop().create_future()
            self._pending[req.gate_id] = (req, future)
            if self.interactive_console:
                self._console_task = asyncio.create_task(self._ask_console(req))
            try:
                decision = await asyncio.wait_for(
                    asyncio.shield(future), timeout=self._config.gate_timeout_minutes * 60)
            except TimeoutError:
                action: Action = "stop" if self._config.on_timeout == "stop" else "continue"
                decision = GateDecision(action, f"no answer within {self._config.gate_timeout_minutes:g} min",
                                        decided_by="timeout", gate_id=req.gate_id)
            finally:
                self._pending.pop(req.gate_id, None)
                if self._console_task and not self._console_task.done():
                    self._console_task.cancel()

        await self._publish("human_gate_resolved", **decision.to_dict(), source=source, title=title)
        self._record(req, decision)
        return decision

    def resolve(self, gate_id: str, action: str, message: str = "", *, decided_by: str = "human",
                rerun: list[str] | None = None) -> bool:
        """Answer an open gate. Returns False when no such gate is waiting."""
        entry = self._pending.get(gate_id)
        if entry is None or entry[1].done():
            return False
        options = entry[0].options
        if action not in options:
            raise ValueError(f"action must be one of {tuple(options)}, got {action!r}")
        if action in NEEDS_MESSAGE and not message.strip():
            raise ValueError(f"'{action}' needs a message")
        rerun = [p for p in RERUN_PHASES if p in (rerun or [])] if action == "propose" else []
        entry[1].set_result(GateDecision(action, message, decided_by=decided_by, gate_id=gate_id,  # type: ignore[arg-type]
                                         rerun=rerun))
        return True

    def close_source(self, source: str, reason: str, *, decided_by: str = "system") -> None:
        """Close the open gates of *source* that no longer matter (the research phase ended, or
        the live auditor withdrew its question); the campaign goes on."""
        for gate_id, (req, _) in list(self._pending.items()):
            if req.source == source:
                self.resolve(gate_id, "continue", reason, decided_by=decided_by)

    def cancel_all(self, reason: str = "campaign stopped") -> None:
        for gate_id in list(self._pending):
            self.resolve(gate_id, "stop", reason, decided_by="system")

    # -- helpers ---------------------------------------------------------------

    async def _ask_console(self, req: GateRequest) -> None:
        """Ask in the terminal (``aurora run`` in an interactive shell)."""
        propose = "propose" in req.options
        prompt = (f"\n=== Human gate: {req.title} ===\n{req.detail}\n"
                  "Answer: [c]ontinue, [i]nstruct (then type the instruction), "
                  + ("[p]ropose (then type the direction on one line), " if propose else "") + "[s]top > ")
        while req.gate_id in self._pending:
            answer = (await asyncio.to_thread(_read_line, prompt)).strip().lower()
            if answer in ("c", "continue"):
                self.resolve(req.gate_id, "continue")
            elif answer in ("s", "stop"):
                self.resolve(req.gate_id, "stop")
            elif answer in ("i", "instruct"):
                text = await asyncio.to_thread(_read_line, "Instruction > ")
                self.resolve(req.gate_id, "instruct", text.strip())
            elif propose and answer in ("p", "propose"):
                text = (await asyncio.to_thread(_read_line, "Direction > ")).strip()
                if not text:
                    continue
                again = (await asyncio.to_thread(
                    _read_line, "Run it through [i]deation, [s]creening, [b]oth or [n]either first? > ")).strip().lower()
                rerun = {"i": ["ideation"], "s": ["screening"], "b": ["ideation", "screening"]}.get(again[:1], [])
                self.resolve(req.gate_id, "propose", text, rerun=rerun)
            else:
                continue
            return

    def _record(self, req: GateRequest, decision: GateDecision) -> None:
        if self._log_path is None:
            return
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._log_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"request": req.to_dict(), "decision": decision.to_dict(),
                                     "resolved_at": time.time()}, ensure_ascii=False) + "\n")
        except OSError:
            logger.warning("Could not record human gate %s", req.gate_id, exc_info=True)


def _read_line(prompt: str) -> str:
    sys.stdout.write(prompt)
    sys.stdout.flush()
    return sys.stdin.readline()
