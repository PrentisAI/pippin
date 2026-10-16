"""Researcher agent — autonomous tool-use loop (Section C.1, Appendix M.3).

The researcher drives computation by autonomously calling tools (DFT, MLIP,
ASE, literature, analysis), recording observations, proposing claims, and
deciding when to continue, replan, or hand off.

Architecture
------------
Instead of the legacy prompt→JSON→engine-executes pattern, the researcher
uses Claude's tool_use API in an agentic loop:

    system prompt + context → Claude calls tools autonomously → observe results
    → Claude decides next tool call → ... → Claude signals iteration decision

The 8-stage inner loop is now Claude's internal reasoning, not hardcoded stages.
"""

from __future__ import annotations

import logging
from typing import Any

from aurora.agents.base import (
    AgentActionType,
    AgentContext,
    AgentRole,
    AgenticAgent,
)
from aurora.config import PipelineStage
from aurora.llm.router import LLMRouter
from aurora.llm.tool_runner import HookInterceptor, StreamCallback, ToolSchema
from aurora.state.artifacts import ArtifactStore
from aurora.tools.agent_tools import build_agent_tools
from aurora.tools.base import ToolRegistry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

RESEARCHER_SYSTEM_PROMPT = """\
You are a computational materials scientist executing research within the \
AURORA framework. You have direct access to computational tools and \
should use them autonomously to advance the research goal.

## Your Capabilities
You can directly:
- **Build atomic structures** (ASE tool) — slabs, bulk cells, adsorbates, dopants
- **Run DFT calculations** (DFT tool) — SCF, relaxation, vc-relax via Quantum ESPRESSO
- **Run MLIP predictions** (MLIP tool) — fast energy/force evaluation via MACE/CHGNet
- **Search literature** — query arXiv and Semantic Scholar
- **Run custom analysis** (run_analysis_script) — write and execute Python scripts
- **Record observations** — capture scientific findings with numerical values
- **Record claims** — propose scientific claims with supporting evidence
- **Read current state** (read_artifacts) — review experiments, observations, claims

## Research Methodology
For each iteration, you should:
1. Read the current research artifacts to understand where things stand
2. Identify the most promising next experiment based on the goal and evidence
3. Set up and execute the computation
4. Analyze the results — check convergence, consistency, anomalies
5. Record observations with proper evidence citations
6. Update your scientific understanding — propose or revise claims
7. Decide the next step: continue, replan, request audit, or declare done

## Evidence Discipline
- ALWAYS cite evidence by experiment/observation IDs
- CLEARLY distinguish confirmed findings from unverified hypotheses
- FLAG any contradictions with existing evidence
- When confidence is low (< 0.3), use the request_audit tool

## Domain Expertise
You have expert knowledge in:
- Density Functional Theory (DFT) calculations
- Machine Learning Interatomic Potentials (MLIPs)
- Surface science and catalysis
- Statistical mechanics of alloys
- Convergence testing methodology

## Important
- Always call decide_next_step at the end to signal your iteration decision
- If a calculation fails, diagnose the issue and try a different approach
- If you encounter unexpected results, verify with a different method before claiming
- Keep your tool calls efficient — don't repeat identical calculations"""


# ---------------------------------------------------------------------------
# Agent implementation
# ---------------------------------------------------------------------------


class ResearcherAgent(AgenticAgent):
    """Researcher agent with autonomous tool-use loop.

    The researcher uses Claude's tool_use API to directly call computational
    tools (DFT, ASE, MLIP, etc.), record observations, and decide next steps
    — all within a single agentic loop per iteration.
    """

    role = AgentRole.RESEARCHER
    pipeline_stage = PipelineStage.RESEARCHER

    def __init__(
        self,
        router: LLMRouter,
        agent_id: str | None = None,
        *,
        tool_registry: ToolRegistry | None = None,
        artifact_store: ArtifactStore | None = None,
        workspace_dir: str = ".",
        max_turns: int = 30,
        hook_interceptor: HookInterceptor | None = None,
        user_message_queue: "asyncio.Queue[str] | None" = None,
        stream_callback: StreamCallback | None = None,
    ) -> None:
        import asyncio
        super().__init__(
            router, agent_id,
            max_turns=max_turns,
            hook_interceptor=hook_interceptor,
            user_message_queue=user_message_queue,
            stream_callback=stream_callback,
        )
        self._tool_registry = tool_registry
        self._artifact_store = artifact_store
        self._workspace_dir = workspace_dir
        self._cached_tools: list[ToolSchema] | None = None

    def system_prompt(self) -> str:
        return RESEARCHER_SYSTEM_PROMPT

    def tools(self) -> list[ToolSchema]:
        """Build tool schemas from the tool registry + meta-tools."""
        if self._cached_tools is not None:
            return self._cached_tools

        if self._tool_registry is None or self._artifact_store is None:
            logger.warning(
                "ResearcherAgent has no tool_registry or artifact_store — "
                "returning empty tools (will fall back to text-only mode)"
            )
            return []

        self._cached_tools = build_agent_tools(
            self._tool_registry,
            self._artifact_store,
            self._workspace_dir,
        )
        return self._cached_tools

    async def run(self, context: AgentContext) -> list:
        """Execute one research iteration via the agentic tool-use loop."""
        # Enrich context with available tool names
        if self._tool_registry:
            context.available_tools = self._tool_registry.list_tools() + [
                "record_observation", "record_claim", "read_artifacts",
                "decide_next_step", "request_audit", "run_analysis_script",
            ]

        return await super().run(context)
