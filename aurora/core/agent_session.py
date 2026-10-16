"""Generic Claude Code agent session for pipeline phases.

Wraps the agent loop drivers (ClaudeCodeLoop, OpenAIAgenticLoop,
AnthropicAgenticLoop) into a simple run-and-return interface for
non-research phases like Planning, Analysis, and Writing.

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
import os
from typing import Any

from aurora.config import AuroraConfig
from aurora.core.events import Event, EventBus
from aurora.llm.tool_runner import StreamEvent

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
    ) -> None:
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
        driver = self._resolve_driver()

        async def stream_callback(event: StreamEvent) -> None:
            await self._bus.publish(Event(
                name=event.event_type,
                data=event.data,
                source=f"agent_session:{self._phase_name}",
            ))

        if driver == "claude-code":
            from aurora.llm.agent_loops import ClaudeCodeLoop
            base_url = ""
            if self._config.terminal_base_url and "openrouter.ai" in self._config.terminal_base_url:
                base_url = "https://openrouter.ai/api"
            return ClaudeCodeLoop(
                model=self._model_override or self._config.terminal_model or self._config.llm_assignment.researcher,
                api_key=self._config.terminal_api_key or os.environ.get("OPENROUTER_API_KEY", ""),
                base_url=base_url,
                max_turns=self._max_turns,
                docker_container=self._docker_container,
                cwd=self._cwd,
                stream_callback=stream_callback,
                live_transcript_path=self._live_transcript_path,
                resume_session_id=self._resume_session_id,
                allow_host_bash=self._config.allow_host_bash,
            )
        elif driver == "anthropic":
            from aurora.llm.agent_loops import AnthropicAgenticLoop
            return AnthropicAgenticLoop(
                api_key=self._config.terminal_api_key or os.environ.get("ANTHROPIC_API_KEY", ""),
                model=self._config.terminal_model or self._config.llm_assignment.researcher,
                tools=[],
                max_turns=self._max_turns,
                stream_callback=stream_callback,
            )
        else:
            from aurora.llm.agent_loops import OpenAIAgenticLoop
            return OpenAIAgenticLoop(
                api_key=self._config.terminal_api_key or os.environ.get("OPENROUTER_API_KEY", ""),
                model=self._config.terminal_model or self._config.llm_assignment.researcher,
                tools=[],
                base_url=self._config.terminal_base_url,
                max_turns=self._max_turns,
                stream_callback=stream_callback,
            )

    def _resolve_driver(self) -> str:
        driver = self._config.terminal_driver
        if driver != "auto":
            return driver
        base = self._config.terminal_base_url
        if not base or "anthropic.com" in base:
            return "anthropic"
        # same resolution as the research loop (TerminalIteration)
        return "claude-code"
