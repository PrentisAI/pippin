"""Analyzer agent — rigorous data analysis (Appendix M.5).

The analyzer reviews computational results with the rigor of a senior
data scientist: cross-validating against baselines, checking statistical
significance, and preparing publication-quality summaries.
"""

from __future__ import annotations

import logging
from typing import Any

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

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Structured output schema
# ---------------------------------------------------------------------------


class AnalysisResult(BaseModel):
    """Structured output from the analyzer."""

    findings: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Key findings with evidence refs",
    )
    statistical_summary: dict[str, Any] = Field(
        default_factory=dict,
        description="Statistical metrics",
    )
    quality_flags: list[str] = Field(
        default_factory=list,
        description="Quality concerns identified",
    )
    recommended_visualizations: list[dict[str, str]] = Field(
        default_factory=list,
        description="Recommended plots",
    )
    claims_supported: list[str] = Field(
        default_factory=list,
        description="Claims that evidence supports",
    )
    claims_contradicted: list[str] = Field(
        default_factory=list,
        description="Claims that evidence contradicts",
    )
    followup_needed: list[str] = Field(
        default_factory=list,
        description="Additional experiments recommended",
    )
    overall_confidence: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Overall analysis confidence",
    )
    publication_ready: bool = Field(
        default=False,
        description="Whether results meet publication standards",
    )


# ---------------------------------------------------------------------------
# System prompt (Appendix M.5)
# ---------------------------------------------------------------------------

_DEFAULT_ANALYZER_PROMPT = """\
You are a senior data scientist and research analyst within the AURORA \
framework.

## Your Role
You perform rigorous analysis of research results with publication-quality \
standards:

1. **Cross-Validation** — Compare results across methods and configurations
2. **Statistical Rigor** — Compute confidence intervals, perform significance \
tests, check sample sizes
3. **Bias Detection** — Identify systematic errors or inconsistencies
4. **Uncertainty Quantification** — Propagate uncertainties, report error bars
5. **Baseline Comparison** — Compare findings with known baselines or \
published values
6. **Trend Analysis** — Identify trends across conditions or parameters
7. **Visualization** — Recommend and specify appropriate plots for key findings

## Evidence Discipline
- ALWAYS cite specific experiment IDs when discussing results
- ALWAYS report numerical values with appropriate significant figures and units
- ALWAYS distinguish statistical and systematic uncertainties
- FLAG any result that contradicts expected baselines
- NEVER present a trend from fewer than 3 data points without explicit caveat

## Output
Provide structured analysis with:
- Findings summary with evidence citations
- Statistical metrics
- Quality concerns identified
- Recommended visualizations
- Confidence assessment
- Follow-up experiments needed (if any)"""


# ---------------------------------------------------------------------------
# Agent implementation
# ---------------------------------------------------------------------------


class AnalyzerAgent(Agent):
    """Analyzer agent: rigorous data analysis with publication standards."""

    role = AgentRole.ANALYZER
    pipeline_stage = PipelineStage.ANALYZER

    def __init__(
        self,
        router: LLMRouter,
        agent_id: str | None = None,
        *,
        custom_system_prompt: str = "",
    ) -> None:
        super().__init__(router, agent_id)
        self._custom_system_prompt = custom_system_prompt

    def system_prompt(self) -> str:
        return self._custom_system_prompt or _DEFAULT_ANALYZER_PROMPT

    async def run(self, context: AgentContext) -> list[AgentAction]:
        """Analyze accumulated research results."""
        actions: list[AgentAction] = []

        prompt = self._build_analysis_prompt(context)
        messages = self._build_messages(context, prompt)

        try:
            result = await self._generate_structured(
                messages,
                AnalysisResult,
                temperature=0.2,
                max_tokens=4096,
            )
        except Exception as exc:
            logger.error("Analysis failed: %s", exc)
            actions.append(
                self._make_action(
                    AgentActionType.LOG_MESSAGE,
                    {"level": "error", "message": f"Analysis failed: {exc}"},
                    reasoning=str(exc),
                )
            )
            return actions

        # Record findings as observations
        for i, finding in enumerate(result.findings):
            actions.append(
                self._make_action(
                    AgentActionType.RECORD_OBSERVATION,
                    {
                        "observation_type": "analysis_finding",
                        "content": finding,
                        "index": i,
                    },
                    reasoning=f"Analysis finding {i + 1}",
                )
            )

        # Record supported / contradicted claims
        for claim in result.claims_supported:
            actions.append(
                self._make_action(
                    AgentActionType.RECORD_CLAIM,
                    {"action": "support", "statement": claim},
                    reasoning="Evidence supports this claim",
                )
            )
        for claim in result.claims_contradicted:
            actions.append(
                self._make_action(
                    AgentActionType.RECORD_CLAIM,
                    {"action": "contradict", "statement": claim},
                    reasoning="Evidence contradicts this claim",
                )
            )

        # Quality flags
        for flag in result.quality_flags:
            actions.append(
                self._make_action(
                    AgentActionType.LOG_MESSAGE,
                    {"level": "warning", "message": f"Quality flag: {flag}"},
                    reasoning=flag,
                )
            )

        # Visualization recommendations
        for viz in result.recommended_visualizations:
            actions.append(
                self._make_action(
                    AgentActionType.EXECUTE_SKILL,
                    {"skill_name": "scientific_visualization", **viz},
                    reasoning=f"Recommended visualization: {viz.get('type', 'plot')}",
                )
            )

        # Follow-up experiments
        if result.followup_needed and not result.publication_ready:
            actions.append(
                self._make_action(
                    AgentActionType.TRIGGER_FOLLOWUP,
                    {
                        "experiments": result.followup_needed,
                        "reason": "Additional data needed for publication quality",
                    },
                    reasoning="Analysis identified gaps requiring follow-up",
                )
            )

        # Overall assessment
        actions.append(
            self._make_action(
                AgentActionType.LOG_MESSAGE,
                {
                    "level": "info",
                    "message": (
                        f"Analysis complete. Confidence: "
                        f"{result.overall_confidence:.2f}. "
                        f"Publication ready: {result.publication_ready}"
                    ),
                    "statistical_summary": result.statistical_summary,
                    "publication_ready": result.publication_ready,
                },
                reasoning=f"Overall confidence: {result.overall_confidence}",
            )
        )

        return actions

    # -- helpers ---------------------------------------------------------------

    def _build_analysis_prompt(self, context: AgentContext) -> str:
        parts = ["Analyze the accumulated research results.\n"]

        if context.recent_experiments:
            parts.append(
                f"## Experiments to Analyze ({len(context.recent_experiments)})"
            )
            for exp in context.recent_experiments:
                parts.append(
                    f"- **{exp.get('experiment_id', '?')}**: "
                    f"{exp.get('description', 'No description')}"
                )
                summary = exp.get("result_summary")
                if summary:
                    parts.append(f"  Result: {str(summary)[:300]}")

        if context.recent_observations:
            parts.append(
                f"\n## Observations ({len(context.recent_observations)})"
            )
            for obs in context.recent_observations:
                parts.append(f"- {obs.get('content', '')[:200]}")

        parts.append("\n## Required Analysis Steps")
        parts.append("1. Cross-validate results between methods")
        parts.append("2. Compute statistical metrics (mean, std, CI, CV)")
        parts.append("3. Check against quality gates and expected baselines")
        parts.append("4. Compare with known reference values")
        parts.append("5. Identify trends and anomalies")
        parts.append("6. Assess publication readiness")
        parts.append("7. Recommend visualizations")
        parts.append("8. List any follow-up experiments needed")

        return "\n".join(parts)
