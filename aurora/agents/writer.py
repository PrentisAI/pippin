"""Writer agent — manuscript writing + follow-up triggering (Appendix M.6).

The writer plans and drafts a scientific manuscript from the accumulated
research evidence.  When critical knowledge gaps are identified, it triggers
a follow-up research cycle.
"""

from __future__ import annotations

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
from aurora.llm.router import LLMRouter
from aurora.models.artifacts import ResearchArtifacts
from aurora.state.artifacts import ProvenanceTracker

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Structured output schemas
# ---------------------------------------------------------------------------


class ManuscriptPlan(BaseModel):
    """Structured plan for the manuscript."""

    title: str = Field(default="Untitled Manuscript", description="Proposed paper title")
    abstract_draft: str = Field(default="", description="Draft abstract")
    sections: list[dict[str, str]] = Field(
        default_factory=list,
        description="Section outlines: [{name, outline, status}]",
    )
    figures_needed: list[str] = Field(
        default_factory=list,
        description="Figures to include",
    )
    tables_needed: list[str] = Field(
        default_factory=list,
        description="Tables to include",
    )
    knowledge_gaps: list[str] = Field(
        default_factory=list,
        description="Missing evidence",
    )
    followup_needed: bool = Field(
        default=False,
        description="Whether follow-up research is needed",
    )
    followup_questions: list[str] = Field(
        default_factory=list,
        description="Specific questions for follow-up",
    )
    completeness: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Estimated manuscript completeness",
    )


class SectionDraft(BaseModel):
    """A drafted section of the manuscript."""

    section_name: str = ""
    content: str = ""
    evidence_cited: list[str] = Field(default_factory=list)
    figures_referenced: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    gaps_in_section: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# System prompt (Appendix M.6)
# ---------------------------------------------------------------------------

WRITER_SYSTEM_PROMPT = """\
You are a scientific manuscript writer within the AURORA framework.

## Your Role
You write publication-quality scientific manuscripts based on completed \
research:

1. **Paper Planning** — Structure the manuscript (title, abstract, sections)
2. **Section Writing** — Write each section with proper scientific style
3. **Citation Integration** — Reference all evidence by experiment/observation IDs
4. **Gap Identification** — Identify missing evidence that would strengthen the paper
5. **Follow-up Triggering** — When gaps are critical, trigger additional research cycles

## Writing Standards
- Use precise, quantitative language
- Report numerical values with units and uncertainties
- Cite specific experiment IDs and observations
- Distinguish confirmed findings from hypotheses
- Follow the structure: Introduction → Methods → Results → Discussion → Conclusion
- Include figure/table references for key data

## Follow-up Decision
After drafting, assess whether the manuscript is complete or needs additional \
data.  If critical gaps exist, trigger a follow-up research cycle specifying \
exactly what's needed.

## Output
Provide structured output with:
- Paper outline / plan
- Section drafts
- Knowledge gaps identified
- Whether follow-up is needed
- Suggested figures and tables"""


# ---------------------------------------------------------------------------
# Agent implementation
# ---------------------------------------------------------------------------


class WriterAgent(Agent):
    """Writer agent: manuscript planning, writing, and follow-up triggering."""

    role = AgentRole.WRITER
    pipeline_stage = PipelineStage.WRITER

    def __init__(
        self,
        router: LLMRouter,
        agent_id: str | None = None,
        *,
        custom_system_prompt: str = "",
    ) -> None:
        super().__init__(router, agent_id)
        self._custom_system_prompt = custom_system_prompt
        self._manuscript_plan: ManuscriptPlan | None = None
        self._drafted_sections: list[SectionDraft] = []

    def system_prompt(self) -> str:
        return self._custom_system_prompt or WRITER_SYSTEM_PROMPT

    async def run(self, context: AgentContext) -> list[AgentAction]:
        """Plan and write manuscript, potentially triggering follow-up."""
        actions: list[AgentAction] = []

        # Provenance validation: warn about ungrounded claims
        artifacts_data = context.extra.get("artifacts")
        if artifacts_data is not None:
            if isinstance(artifacts_data, ResearchArtifacts):
                artifacts = artifacts_data
            else:
                artifacts = ResearchArtifacts.model_validate(artifacts_data)
            tracker = ProvenanceTracker(artifacts)
            ungrounded = tracker.validate_all_claims()
            if ungrounded:
                actions.append(self._make_action(
                    AgentActionType.LOG_MESSAGE,
                    payload={
                        "level": "warning",
                        "message": f"Ungrounded claims detected: {ungrounded}",
                    },
                    reasoning="Provenance validation found claims without supporting evidence",
                ))

        # Phase 1: Plan the manuscript
        plan = await self._plan_manuscript(context)
        self._manuscript_plan = plan

        actions.append(
            self._make_action(
                AgentActionType.WRITE_ARTIFACT,
                {
                    "artifact_type": "manuscript_plan",
                    "title": plan.title,
                    "plan": plan.model_dump(),
                },
                reasoning=f"Manuscript plan: {plan.title}",
            )
        )

        # Phase 2: Write each section
        for section_info in plan.sections:
            section_name = section_info.get("name", "Untitled")
            draft = await self._write_section(context, section_name, plan)
            self._drafted_sections.append(draft)

            actions.append(
                self._make_action(
                    AgentActionType.WRITE_MANUSCRIPT,
                    {
                        "section_name": draft.section_name,
                        "content": draft.content,
                        "evidence_cited": draft.evidence_cited,
                        "figures_referenced": draft.figures_referenced,
                        "confidence": draft.confidence,
                    },
                    reasoning=f"Drafted section: {draft.section_name}",
                )
            )

        # Phase 3: Check for follow-up needs
        if plan.followup_needed and plan.followup_questions:
            actions.append(
                self._make_action(
                    AgentActionType.TRIGGER_FOLLOWUP,
                    {
                        "reason": "Knowledge gaps identified during writing",
                        "questions": plan.followup_questions,
                        "gaps": plan.knowledge_gaps,
                        "completeness": plan.completeness,
                    },
                    reasoning=(
                        f"Follow-up needed: {len(plan.followup_questions)} "
                        f"questions, completeness: {plan.completeness:.0%}"
                    ),
                )
            )

        # Phase 4: Final assessment
        actions.append(
            self._make_action(
                AgentActionType.LOG_MESSAGE,
                {
                    "level": "info",
                    "message": (
                        f"Manuscript '{plan.title}' drafted. "
                        f"Sections: {len(self._drafted_sections)}. "
                        f"Completeness: {plan.completeness:.0%}. "
                        f"Follow-up: {plan.followup_needed}"
                    ),
                    "completeness": plan.completeness,
                },
                reasoning="Writing complete",
            )
        )

        return actions

    # -- private helpers -------------------------------------------------------

    async def _plan_manuscript(self, context: AgentContext) -> ManuscriptPlan:
        prompt = (
            "Plan a scientific manuscript based on the completed research.\n\n"
            "Provide:\n"
            "1. A concise, descriptive title\n"
            "2. Draft abstract (150-250 words)\n"
            "3. Section outline with key points for each section\n"
            "4. List of figures and tables needed\n"
            "5. Any knowledge gaps that would weaken the paper\n"
            "6. Whether follow-up research is needed to fill critical gaps\n\n"
            "The manuscript should follow standard computational materials "
            "science format:\n"
            "Introduction → Computational Methods → Results → Discussion → "
            "Conclusion"
        )
        messages = self._build_messages(context, prompt)

        try:
            return await self._generate_structured(
                messages, ManuscriptPlan, temperature=0.3, max_tokens=4096
            )
        except Exception as exc:
            logger.error("Manuscript planning failed: %s", exc)
            return ManuscriptPlan(
                title="[Planning Failed]",
                sections=[
                    {"name": "Error", "outline": str(exc), "status": "failed"}
                ],
                followup_needed=False,
                completeness=0.0,
            )

    async def _write_section(
        self,
        context: AgentContext,
        section_name: str,
        plan: ManuscriptPlan,
    ) -> SectionDraft:
        prompt = (
            f"Write the **{section_name}** section of the manuscript.\n\n"
            f"**Paper title:** {plan.title}\n"
            f"**Abstract:** {plan.abstract_draft[:500]}\n\n"
            f"Requirements:\n"
            f"- Use precise, quantitative language\n"
            f"- Cite experiment IDs and observation IDs\n"
            f"- Include references to figures/tables as appropriate\n"
            f"- Report uncertainties and confidence levels\n"
            f"- Distinguish confirmed results from interpretations\n\n"
            f"Write the complete section text."
        )
        messages = self._build_messages(context, prompt)

        try:
            return await self._generate_structured(
                messages, SectionDraft, temperature=0.4, max_tokens=4096
            )
        except Exception as exc:
            logger.warning(
                "Section writing failed for %s: %s", section_name, exc
            )
            return SectionDraft(
                section_name=section_name,
                content=f"[Writing failed: {exc}]",
                confidence=0.0,
                gaps_in_section=[str(exc)],
            )
