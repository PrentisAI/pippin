"""Base agent abstraction for Pippin.

Every agent receives an :class:`AgentContext` (reconstructed on demand, never
persisted wholesale) and returns one or more :class:`AgentAction` objects that
the engine interprets.

Two base classes are provided:

- :class:`Agent` — classic prompt-and-parse agents (auditor, roundtable, etc.)
"""

from __future__ import annotations

import logging
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, TypeVar

from pydantic import BaseModel

from aurora.config import PipelineStage
from aurora.llm.base import Message
from aurora.llm.router import LLMRouter

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class AgentRole(str, Enum):
    """Roles an agent can assume in the Pippin pipeline."""

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
    """Abstract base class for all Pippin agents.

    Each agent:
    1. Receives a reconstructed context (never the full workspace)
    2. Uses the LLM router for generation (never instantiates providers)
    3. Returns actions for the engine to execute
    4. Has a system prompt derived from the paper's appendix "Prompts for Agents"
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
        """Return the agent's system prompt (from the paper's appendix "Prompts for Agents")."""
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
        self, messages: list[Message], schema: type[T], **kwargs
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
