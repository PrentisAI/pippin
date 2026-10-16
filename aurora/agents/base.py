"""Base agent abstraction for AURORA.

Every agent receives an :class:`AgentContext` (reconstructed on demand, never
persisted wholesale) and returns one or more :class:`AgentAction` objects that
the engine interprets.

Two base classes are provided:

- :class:`Agent` — classic prompt-and-parse agents (auditor, roundtable, etc.)
- :class:`AgenticAgent` — tool-use loop agents that autonomously call tools
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Type, TypeVar

from pydantic import BaseModel

from aurora.config import PipelineStage
from aurora.llm.base import Message
from aurora.llm.router import LLMRouter
from aurora.llm.tool_runner import (
    AgenticLoop,
    AgenticResult,
    HookInterceptor,
    StreamCallback,
    ToolSchema,
)

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class AgentRole(str, Enum):
    """Roles an agent can assume in the AURORA pipeline."""

    RESEARCHER = "researcher"
    AUDITOR = "auditor"
    ANALYZER = "analyzer"
    WRITER = "writer"
    ROUNDTABLE_SEAT = "roundtable_seat"
    ROUNDTABLE_CHAIR = "roundtable_chair"


class AgentActionType(str, Enum):
    """Discriminated union tag for agent actions (Section C).

    Each variant corresponds to one kind of side-effect that the engine
    knows how to execute.
    """

    EXECUTE_SKILL = "execute_skill"
    EXECUTE_TOOL = "execute_tool"
    WRITE_ARTIFACT = "write_artifact"
    UPDATE_ARTIFACT = "update_artifact"
    RECORD_OBSERVATION = "record_observation"
    RECORD_CLAIM = "record_claim"
    PROPOSE_GOAL_CHANGE = "propose_goal_change"
    REQUEST_AUDIT = "request_audit"
    TRIGGER_FOLLOWUP = "trigger_followup"
    SUBMIT_CONTRIBUTION = "submit_contribution"
    JUDGE_CONVERGENCE = "judge_convergence"
    PROPOSE_IDEA = "propose_idea"
    WRITE_MANUSCRIPT = "write_manuscript"
    REQUEST_HUMAN_INPUT = "request_human_input"
    LOG_MESSAGE = "log_message"
    TERMINATE = "terminate"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class AgentAction:
    """A single action emitted by an agent for the engine to execute."""

    action_type: AgentActionType
    payload: dict[str, Any] = field(default_factory=dict)
    agent_role: AgentRole | None = None
    agent_id: str = ""
    timestamp: datetime = field(default_factory=datetime.utcnow)
    reasoning: str = ""

    def __post_init__(self) -> None:
        if not self.agent_id:
            self.agent_id = str(uuid.uuid4())[:8]


@dataclass
class AgentContext:
    """Runtime context passed to an agent on each invocation.

    This is reconstructed on demand by the ContextReconstructor (Section E.2).
    Agents should NOT persist this object.  The context always begins with the
    current research goal and relevant subset of artifacts.
    """

    campaign_id: str
    current_goal: str
    research_summary: str = ""
    mental_model_summary: str = ""
    recent_experiments: list[dict[str, Any]] = field(default_factory=list)
    recent_observations: list[dict[str, Any]] = field(default_factory=list)
    active_claims: list[dict[str, Any]] = field(default_factory=list)
    contradictions: list[str] = field(default_factory=list)
    recent_failures: list[str] = field(default_factory=list)
    audit_findings: list[dict[str, Any]] = field(default_factory=list)
    available_skills: list[str] = field(default_factory=list)
    available_tools: list[str] = field(default_factory=list)
    iteration_number: int = 0
    extra: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class Agent(ABC):
    """Abstract base class for all AURORA agents.

    Each agent:
    1. Receives a reconstructed context (never the full workspace)
    2. Uses the LLM router for generation (never instantiates providers)
    3. Returns actions for the engine to execute
    4. Has a system prompt derived from Appendix M of the paper
    """

    role: AgentRole
    pipeline_stage: PipelineStage

    def __init__(self, router: LLMRouter, agent_id: str | None = None) -> None:
        self._router = router
        self._agent_id = agent_id or f"{self.role.value}_{uuid.uuid4().hex[:6]}"
        self._action_history: list[AgentAction] = []

    @property
    def agent_id(self) -> str:
        return self._agent_id

    # -- abstract interface ----------------------------------------------------

    @abstractmethod
    def system_prompt(self) -> str:
        """Return the agent's system prompt (from Appendix M)."""
        ...

    @abstractmethod
    async def run(self, context: AgentContext) -> list[AgentAction]:
        """Execute one step and return actions for the engine.

        Parameters
        ----------
        context:
            Reconstructed context for this invocation.

        Returns
        -------
        list[AgentAction]
            Actions for the engine to execute.  May be empty if
            the agent decides no action is needed.
        """
        ...

    # -- LLM helpers -----------------------------------------------------------

    async def _generate(self, messages: list[Message], **kwargs) -> str:
        """Convenience: generate text via the router for this agent's stage."""
        response = await self._router.generate(
            self.pipeline_stage, messages, **kwargs
        )
        return response.content

    async def _generate_structured(
        self, messages: list[Message], schema: Type[T], **kwargs
    ) -> T:
        """Convenience: generate structured output via the router."""
        return await self._router.generate_structured(
            self.pipeline_stage, messages, schema, **kwargs
        )

    # -- action helpers --------------------------------------------------------

    def _make_action(
        self,
        action_type: AgentActionType,
        payload: dict[str, Any] | None = None,
        reasoning: str = "",
    ) -> AgentAction:
        """Create an action pre-filled with this agent's metadata."""
        action = AgentAction(
            action_type=action_type,
            payload=payload or {},
            agent_role=self.role,
            agent_id=self._agent_id,
            reasoning=reasoning,
        )
        self._action_history.append(action)
        return action

    # -- context formatting ----------------------------------------------------

    def _build_messages(
        self, context: AgentContext, user_prompt: str
    ) -> list[Message]:
        """Build a message list with system prompt + context + user prompt."""
        context_text = self._format_context(context)
        return [
            Message.system(self.system_prompt()),
            Message.user(
                f"## Current Research Context\n\n{context_text}"
                f"\n\n## Task\n\n{user_prompt}"
            ),
        ]

    def _format_context(self, context: AgentContext) -> str:
        """Format an AgentContext into a text block for the LLM."""
        parts: list[str] = [
            f"**Campaign:** {context.campaign_id}",
            f"**Current Goal:** {context.current_goal}",
            f"**Iteration:** {context.iteration_number}",
        ]

        if context.research_summary:
            parts.append(f"\n### Research Summary\n{context.research_summary}")
        if context.mental_model_summary:
            parts.append(
                f"\n### Scientific Mental Model\n{context.mental_model_summary}"
            )
        if context.contradictions:
            parts.append("\n### Active Contradictions")
            for c in context.contradictions:
                parts.append(f"- {c}")
        if context.recent_failures:
            parts.append("\n### Recent Failures")
            for f in context.recent_failures:
                parts.append(f"- {f}")
        if context.recent_experiments:
            parts.append(
                f"\n### Recent Experiments ({len(context.recent_experiments)})"
            )
            for exp in context.recent_experiments[-5:]:
                parts.append(
                    f"- {exp.get('experiment_id', '?')}: "
                    f"{exp.get('description', '')[:200]}"
                )
        if context.active_claims:
            parts.append(f"\n### Active Claims ({len(context.active_claims)})")
            for cl in context.active_claims:
                parts.append(
                    f"- [{cl.get('status', '?')}] "
                    f"{cl.get('statement', '')[:200]}"
                )
        if context.audit_findings:
            parts.append(
                f"\n### Audit Findings ({len(context.audit_findings)})"
            )
            for af in context.audit_findings[-3:]:
                parts.append(
                    f"- [{af.get('outcome', '?')}] "
                    f"{af.get('finding', '')[:200]}"
                )
        if context.available_skills:
            parts.append(
                f"\n### Available Skills: {', '.join(context.available_skills)}"
            )
        if context.available_tools:
            parts.append(
                f"\n### Available Tools: {', '.join(context.available_tools)}"
            )
        return "\n".join(parts)


# ---------------------------------------------------------------------------
# Agentic base (tool-use loop)
# ---------------------------------------------------------------------------


class AgenticAgent(Agent):
    """Base class for agents that use Claude's tool_use in an autonomous loop.

    Instead of returning AgentActions for the engine to execute, an
    AgenticAgent calls tools directly during its run and returns
    an :class:`AgenticResult` summarizing what it did.

    Subclasses must implement :meth:`system_prompt` and :meth:`tools`.
    The :meth:`run` method is implemented here using :class:`AgenticLoop`.
    """

    def __init__(
        self,
        router: LLMRouter,
        agent_id: str | None = None,
        *,
        max_turns: int = 30,
        hook_interceptor: HookInterceptor | None = None,
        user_message_queue: asyncio.Queue | None = None,
        stream_callback: StreamCallback | None = None,
    ) -> None:
        super().__init__(router, agent_id)
        self._max_turns = max_turns
        self._hook_interceptor = hook_interceptor
        self._user_queue = user_message_queue
        self._stream_callback = stream_callback
        self._last_agentic_result: AgenticResult | None = None

    @abstractmethod
    def tools(self) -> list[ToolSchema]:
        """Return the tool schemas available to this agent."""
        ...

    @property
    def last_agentic_result(self) -> AgenticResult | None:
        """The result of the most recent agentic loop run."""
        return self._last_agentic_result

    async def run(self, context: AgentContext) -> list[AgentAction]:
        """Execute one iteration via an autonomous tool-use loop.

        The agent calls tools directly (DFT, ASE, record_observation, etc.)
        and returns a summary as AgentActions for backward compatibility with
        the engine's action-processing layer.
        """
        provider, model = self._router.get_provider_and_model(self.pipeline_stage)

        # Resolve API key
        api_key = ""
        if hasattr(provider, "_api_key"):
            api_key = provider._api_key
        if not api_key:
            api_key = os.environ.get("ANTHROPIC_API_KEY", "")

        loop = AgenticLoop(
            api_key=api_key,
            model=model,
            tools=self.tools(),
            max_turns=self._max_turns,
            usage_tracker=self._router.usage_tracker,
            hook_interceptor=self._hook_interceptor,
            user_message_queue=self._user_queue,
            stream_callback=self._stream_callback,
        )

        context_text = self._format_context(context)
        user_message = (
            f"## Research Context\n\n{context_text}\n\n"
            f"## Task\n\n"
            f"Execute one research iteration. Use the available tools to:\n"
            f"1. Review current state (read_artifacts)\n"
            f"2. Design and run experiments\n"
            f"3. Analyze results\n"
            f"4. Record observations and claims\n"
            f"5. Decide the next step\n\n"
            f"When you're done, call decide_next_step with your decision."
        )

        try:
            result = await loop.run(
                system_prompt=self.system_prompt(),
                user_message=user_message,
            )
            self._last_agentic_result = result
        finally:
            await loop.close()

        return self._agentic_result_to_actions(result)

    def _agentic_result_to_actions(self, result: AgenticResult) -> list[AgentAction]:
        """Convert an AgenticResult to AgentActions for engine compatibility."""
        actions: list[AgentAction] = []

        # Record each observation as an action
        for obs in result.observations:
            actions.append(self._make_action(
                AgentActionType.RECORD_OBSERVATION,
                obs,
                reasoning="Recorded via agentic tool call",
            ))

        # Record each claim as an action
        for claim in result.claims:
            actions.append(self._make_action(
                AgentActionType.RECORD_CLAIM,
                claim,
                reasoning="Proposed via agentic tool call",
            ))

        # Map decision to action type
        decision_map = {
            "continue": AgentActionType.LOG_MESSAGE,
            "replan": AgentActionType.PROPOSE_GOAL_CHANGE,
            "audit": AgentActionType.REQUEST_AUDIT,
            "done": AgentActionType.TERMINATE,
            "follow_up": AgentActionType.TRIGGER_FOLLOWUP,
        }
        action_type = decision_map.get(result.decision, AgentActionType.LOG_MESSAGE)
        actions.append(self._make_action(
            action_type,
            {
                "decision": result.decision,
                "summary": result.final_text[:500],
                "tool_calls_count": len(result.tool_calls),
                "turns": result.turns,
            },
            reasoning=result.final_text[:300],
        ))

        return actions
