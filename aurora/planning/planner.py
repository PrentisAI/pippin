"""LLM-driven experiment planner (Phase 6).

The :class:`ExperimentPlanner` takes a screened :class:`RoundtableProposal`
and asks the *researcher* LLM to decompose it into a DAG of computational
jobs, each mapped to a registered skill.  The raw LLM output is validated
through an intermediate Pydantic schema before being converted into a
first-class :class:`ExperimentPlan` with deterministic IDs and timestamps.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from aurora.config import PipelineStage
from aurora.llm.base import Message
from aurora.llm.router import LLMRouter
from aurora.models.experiment import (
    DependencyEdge,
    ExperimentJob,
    ExperimentPlan,
)
from aurora.models.roundtable import RoundtableProposal
from aurora.skills.registry import SkillRegistry
from aurora.state.workspace import WorkspaceManager

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Intermediate LLM response schema
# ---------------------------------------------------------------------------

class _LLMJobSpec(BaseModel):
    """A single job as described by the LLM (before ID assignment)."""

    name: str = Field(default="job_1", description="Short human-readable job name (used as ID seed).")
    skill_name: str = Field(default="structure_construction", description="Registered skill to invoke.")
    parameters: dict[str, Any] = Field(
        default_factory=dict,
        description="Skill-specific parameters.",
    )
    expected_outputs: list[str] = Field(
        default_factory=list,
        description="Filenames or artifact keys the job should produce.",
    )
    resource_requirements: dict[str, Any] = Field(
        default_factory=dict,
        description="Resource needs, e.g. {'gpu': 'H100', 'num_gpus': 1}.",
    )
    depends_on: list[str] = Field(
        default_factory=list,
        description="Names of jobs that must finish before this one starts.",
    )


class _LLMPlanResponse(BaseModel):
    """Structured response from the LLM containing the full plan."""

    reasoning: str = Field(
        default="",
        description="Brief explanation of the decomposition strategy.",
    )
    jobs: list[_LLMJobSpec] = Field(
        default_factory=list,
        description="Ordered list of computational jobs.",
    )


# ---------------------------------------------------------------------------
# Planner
# ---------------------------------------------------------------------------

class ExperimentPlanner:
    """Decomposes a research proposal into an executable experiment plan.

    The planner:

    1. Constructs a prompt containing the proposal details and the list
       of available skills.
    2. Calls the researcher LLM via :pymethod:`LLMRouter.generate_structured`
       to obtain a job decomposition.
    3. Converts the LLM output into a canonical :class:`ExperimentPlan`,
       assigning stable IDs and extracting dependency edges.
    4. Persists the plan to the workspace and emits a trajectory event.

    Parameters
    ----------
    router:
        LLM router for model-routed generation.
    skill_registry:
        Registry of available skills (used to enumerate skill names in
        the prompt).
    workspace:
        Workspace manager for persistence and event logging.
    """

    def __init__(
        self,
        router: LLMRouter,
        skill_registry: SkillRegistry | None,
        workspace: WorkspaceManager,
        *,
        system_prompt: str = "",
    ) -> None:
        self._router = router
        self._skills = skill_registry or SkillRegistry()
        self._workspace = workspace
        self._system_prompt = system_prompt

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def plan(self, proposal: RoundtableProposal) -> ExperimentPlan:
        """Decompose *proposal* into an :class:`ExperimentPlan`.

        Parameters
        ----------
        proposal:
            The screened research proposal to plan for.

        Returns
        -------
        ExperimentPlan
            A validated plan with unique job IDs, dependency edges, and
            a monotonically increasing revision counter.

        Raises
        ------
        aurora.llm.base.LLMResponseParseError
            If the LLM response cannot be parsed into the expected schema.
        """
        logger.info("Planning experiment for proposal %s: %s", proposal.proposal_id, proposal.title)

        # 1. Build the prompt
        messages = self._build_prompt(proposal)

        # 2. Ask the LLM to decompose into jobs
        llm_plan: _LLMPlanResponse = await self._router.generate_structured(
            PipelineStage.RESEARCHER,
            messages,
            _LLMPlanResponse,
            temperature=0.4,
        )

        logger.info(
            "LLM proposed %d jobs for proposal %s",
            len(llm_plan.jobs),
            proposal.proposal_id,
        )

        # 3. Convert to canonical ExperimentPlan
        plan = self._build_experiment_plan(proposal, llm_plan)

        # 4. Persist and log
        self._workspace.atomic_write_json(self._workspace.experiment_plan_path, plan)
        self._workspace.append_event({
            "event": "experiment_plan_created",
            "source": "experiment_planner",
            "plan_id": plan.plan_id,
            "proposal_id": proposal.proposal_id,
            "n_jobs": len(plan.jobs),
            "n_edges": len(plan.dependency_edges),
        })

        logger.info(
            "Persisted plan %s (%d jobs, %d edges)",
            plan.plan_id,
            len(plan.jobs),
            len(plan.dependency_edges),
        )
        return plan

    # ------------------------------------------------------------------
    # Prompt construction
    # ------------------------------------------------------------------

    def _build_prompt(self, proposal: RoundtableProposal) -> list[Message]:
        """Assemble the system + user messages for the planner LLM call."""

        available_skills = self._skills.list_skills() if self._skills else []
        skills_block = "\n".join(f"  - {s}" for s in available_skills) if available_skills else "  (none registered)"

        default_system = (
            "You are an expert research planner. "
            "Given a research proposal, decompose it into a directed acyclic graph "
            "(DAG) of concrete computational jobs.\n\n"
            "Rules:\n"
            "1. Each job must use exactly one skill from the available skill list.\n"
            "2. Specify dependencies between jobs using the job *name* field.\n"
            "3. Include all necessary parameters for each skill invocation.\n"
            "4. Order jobs so that data flows forward (no cycles).\n"
            "5. Be as specific as possible about expected outputs and resource needs.\n"
            "6. If no registered skill matches a required step, use the closest "
            "   skill name and note the gap in the parameters."
        )
        system = Message.system(self._system_prompt or default_system)

        user = Message.user(
            f"## Research Proposal\n\n"
            f"**Title:** {proposal.title}\n\n"
            f"**Description:** {proposal.description}\n\n"
            f"**Research Questions:**\n"
            + "\n".join(f"  - {q}" for q in proposal.research_questions)
            + "\n\n"
            f"**Proposed Methods:**\n"
            + "\n".join(f"  - {m}" for m in proposal.proposed_methods)
            + "\n\n"
            f"**Expected Outcomes:**\n"
            + "\n".join(f"  - {o}" for o in proposal.expected_outcomes)
            + "\n\n"
            f"## Available Skills\n\n{skills_block}\n\n"
            f"Decompose this proposal into computational jobs. "
            f"Return your response as structured JSON."
        )

        return [system, user]

    # ------------------------------------------------------------------
    # Plan construction
    # ------------------------------------------------------------------

    def _build_experiment_plan(
        self,
        proposal: RoundtableProposal,
        llm_plan: _LLMPlanResponse,
    ) -> ExperimentPlan:
        """Convert the LLM's response into a canonical :class:`ExperimentPlan`."""

        plan_id = f"plan_{proposal.proposal_id}_{uuid.uuid4().hex[:8]}"

        # Map LLM job names → stable job IDs
        name_to_id: dict[str, str] = {}
        jobs: list[ExperimentJob] = []

        for idx, spec in enumerate(llm_plan.jobs):
            job_id = f"{plan_id}_job{idx:03d}_{_slugify(spec.name)}"
            name_to_id[spec.name] = job_id

            # Resolve dependency names → IDs
            dep_ids: list[str] = []
            for dep_name in spec.depends_on:
                if dep_name in name_to_id:
                    dep_ids.append(name_to_id[dep_name])
                else:
                    logger.warning(
                        "Job %r depends on %r which has not been seen yet — "
                        "skipping this dependency edge",
                        spec.name,
                        dep_name,
                    )

            jobs.append(ExperimentJob(
                job_id=job_id,
                skill_name=spec.skill_name,
                parameters=spec.parameters,
                expected_outputs=spec.expected_outputs,
                resource_requirements=spec.resource_requirements,
                dependencies=dep_ids,
                status="pending",
            ))

        # Build explicit DependencyEdge list from the resolved dependencies
        edges: list[DependencyEdge] = []
        for job in jobs:
            for dep_id in job.dependencies:
                edges.append(DependencyEdge(from_job_id=dep_id, to_job_id=job.job_id))

        return ExperimentPlan(
            plan_id=plan_id,
            proposal_id=proposal.proposal_id,
            jobs=jobs,
            dependency_edges=edges,
            created_at=datetime.now(timezone.utc),
            revision=0,
            revision_reason="Initial plan generated by ExperimentPlanner.",
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _slugify(name: str, max_len: int = 30) -> str:
    """Convert a human-readable name to a short, filesystem-safe slug."""
    slug = name.lower().replace(" ", "_")
    slug = "".join(c for c in slug if c.isalnum() or c == "_")
    return slug[:max_len] or "unnamed"
