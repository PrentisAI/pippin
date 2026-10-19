"""Claude Code session for one pipeline phase.

Wraps :class:`~aurora.llm.agent_loops.ClaudeCodeLoop` into a run-and-return
interface for the phases other than research (screening, planning,
analysis, writing) and for the milestone auditor.

Unlike TerminalIteration (which handles verifier, retry, and job
lifecycle), AgentSession is a single-shot executor: give it a
system prompt + user message, get back the agent's text output.

Each phase starts a fresh session. Cross-phase context is handled
by saving each phase's trajectory to disk — the engine injects
prior transcript paths into the system prompt so the agent can
read them on demand.
"""

from __future__ import annotations

import logging
from typing import Any

from aurora.config import AuroraConfig
from aurora.core.events import Event, EventBus
from aurora.llm.stream import StreamEvent

logger = logging.getLogger(__name__)


class AgentSession:
    """Single-shot Claude Code session for a pipeline phase.

    Parameters
    ----------
    config:
        Campaign configuration (model, driver, API key, etc.).
    event_bus:
        For publishing streaming events to the frontend.
    phase_name:
        Human-readable phase name for event attribution.
    docker_container:
        If set, the agent runs commands inside this Docker container.
    cwd:
        Working directory for the agent session.
    max_turns:
        Maximum tool-use turns for this session.
    """

    def __init__(
        self,
        config: AuroraConfig,
        event_bus: EventBus,
        *,
        phase_name: str = "agent",
        docker_container: str | None = None,
        cwd: str | None = None,
        max_turns: int = 30,
        live_transcript_path: str | None = None,
        model_override: str | None = None,
        resume_session_id: str | None = None,
        max_budget_usd: float | None = None,
        session_callback: Any = None,
    ) -> None:
        self._session_callback = session_callback
        self._max_budget_usd = max_budget_usd
        self._config = config
        self._bus = event_bus
        self._phase_name = phase_name
        self._docker_container = docker_container
        self._cwd = cwd
        self._max_turns = max_turns
        self._current_loop: Any = None
        self._live_transcript_path = live_transcript_path
        self._model_override = model_override
        self._resume_session_id = resume_session_id

    async def inject_message(self, message: str) -> None:
        if self._current_loop and hasattr(self._current_loop, "inject_message"):
            await self._current_loop.inject_message(message)

    async def run(
        self,
        system_prompt: str,
        user_message: str,
        *,
        temperature: float = 0.3,
    ) -> dict[str, Any]:
        """Run a Claude Code session and return the result.

        Returns
        -------
        dict with keys:
            - text: str — the agent's final text output
            - turns: int — number of tool-use turns
            - tokens: dict — token usage
            - task_completed: bool — whether task_complete was called
            - trajectory: list — full agent trajectory
        """
        await self._bus.publish(Event(
            name="agent_session_started",
            data={"phase": self._phase_name},
            source=f"agent_session:{self._phase_name}",
        ))

        loop = self._create_loop()
        self._current_loop = loop

        try:
            result = await loop.run(system_prompt, user_message, temperature=temperature)
            result["session_id"] = getattr(loop, "session_id", None)
        finally:
            self._current_loop = None
            await loop.close()

        await self._bus.publish(Event(
            name="agent_session_complete",
            data={
                "phase": self._phase_name,
                "turns": result.get("turns", 0),
            },
            source=f"agent_session:{self._phase_name}",
        ))

        return result

    def _create_loop(self) -> Any:
        async def stream_callback(event: StreamEvent) -> None:
            await self._bus.publish(Event(
                name=event.event_type,
                data=event.data,
                source=f"agent_session:{self._phase_name}",
            ))

        return make_claude_code_loop(
            self._config,
            model=self._model_override or self._config.models.agent,
            max_turns=self._max_turns,
            docker_container=self._docker_container,
            cwd=self._cwd,
            stream_callback=stream_callback,
            live_transcript_path=self._live_transcript_path,
            resume_session_id=self._resume_session_id,
            max_budget_usd=self._max_budget_usd,
            session_callback=self._session_callback,
        )


def make_claude_code_loop(config: AuroraConfig, *, model: str, max_turns: int,
                          max_budget_usd: float | None = None, **kwargs: Any) -> Any:
    """A :class:`ClaudeCodeLoop` that uses the campaign's endpoint, key and sandbox settings.

    *max_budget_usd* (the campaign's remaining budget) is enforced by Claude Code itself.
    """
    from pathlib import Path

    from aurora.llm.agent_loops import ClaudeCodeLoop
    sb = config.sandbox
    if sb.docker and sb.mount_workspace and kwargs.get("docker_container") and kwargs.get("cwd"):
        kwargs.setdefault("app_on_host", str(Path(kwargs["cwd"]) / "workspace"))
    return ClaudeCodeLoop(
        model=model,
        api_key=config.api_key_value,
        base_url=config.claude_code_base_url,
        max_turns=max_turns,
        allow_host_bash=config.sandbox.allow_host_bash,
        max_budget_usd=max_budget_usd,
        **kwargs,
    )
