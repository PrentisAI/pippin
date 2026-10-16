"""Scientific mental model management (Section E.3).

The mental model is the agent's current *interpretation* of the evidence,
distinct from the persistent research artifacts which are the raw record.
Crucially, the mental model is periodically **reconstructed from the
persistent evidence** rather than evolved from previous model reasoning —
this prevents hallucination drift over long trajectories (paper line 524–525).

Two agents with access to the same experiment history may construct
different mental models and therefore design different next experiments.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Awaitable

from aurora.models.artifacts import ResearchArtifacts
from aurora.models.mental_model import (
    CausalRelationship,
    Hypothesis,
    ScientificMentalModel,
)
from aurora.state.workspace import WorkspaceManager

logger = logging.getLogger(__name__)

# The prompt template used to ask the LLM to reconstruct the mental model.
_RECONSTRUCTION_PROMPT = """\
You are a scientific reasoning engine.  Given the accumulated evidence from \
a computational materials science research campaign, synthesise a structured \
scientific mental model.

## Research Objective
{objective}

## Current Goal
{current_goal}

## Evidence Summary
{evidence_summary}

## Instructions
Based ONLY on the evidence above (not on any prior model or speculation), \
produce a JSON object with the following structure:

```json
{{
  "active_hypotheses": [
    {{
      "hypothesis_id": "h1",
      "statement": "...",
      "status": "active",
      "confidence": 0.0-1.0,
      "supporting_evidence": ["obs_id_1", ...],
      "contradicting_evidence": []
    }}
  ],
  "competing_hypotheses": [],
  "inferred_mechanisms": ["mechanism description 1", ...],
  "causal_relationships": [
    {{
      "cause": "...",
      "effect": "...",
      "mechanism": "...",
      "confidence": 0.0-1.0,
      "evidence_ids": ["obs_id_1", ...]
    }}
  ],
  "unresolved_ambiguity": ["open question 1", ...],
  "expected_outcomes": {{"next experiment description": "expected result"}},
  "current_rationale": "Why the current research goal is worth pursuing."
}}
```

Return ONLY the JSON object, no extra text.
"""


class MentalModelManager:
    """Manages the evolving scientific mental model."""

    def __init__(self, workspace: WorkspaceManager) -> None:
        self._ws = workspace

    # ------------------------------------------------------------------
    # Load / save
    # ------------------------------------------------------------------

    def load(self) -> ScientificMentalModel | None:
        """Load the current mental model from the workspace.

        Returns ``None`` if no model has been persisted yet.
        """
        path = self._ws.mental_model_path
        if not path.exists():
            return None
        raw = path.read_text(encoding="utf-8")
        return ScientificMentalModel.model_validate_json(raw)

    def save(self, model: ScientificMentalModel) -> None:
        """Atomically persist the mental model."""
        self._ws.atomic_write_json(self._ws.mental_model_path, model)

    # ------------------------------------------------------------------
    # Reconstruction from evidence
    # ------------------------------------------------------------------

    async def reconstruct_from_evidence(
        self,
        artifacts: ResearchArtifacts,
        llm_generate: Callable[[str], Awaitable[str]],
    ) -> ScientificMentalModel:
        """Reconstruct the mental model fresh from persistent evidence.

        This is **not** an incremental update of the previous model — it is a
        complete re-synthesis from the evidence record.

        Parameters
        ----------
        artifacts:
            The current persistent research artifacts.
        llm_generate:
            An async callable that takes a prompt string and returns the LLM's
            text response.  The caller is responsible for routing this to the
            correct model (typically the researcher model).

        Returns
        -------
        ScientificMentalModel
            The freshly reconstructed model, already persisted to disk.
        """
        evidence_summary = self._prepare_evidence_summary(artifacts)
        prompt = self._build_reconstruction_prompt(artifacts, evidence_summary)

        logger.info("Reconstructing mental model from %d observations, %d claims",
                     len(artifacts.observations), len(artifacts.claims))

        raw_response = await llm_generate(prompt)
        model = self._parse_model_response(raw_response)

        model.reconstructed_from_evidence_at = datetime.now(timezone.utc)
        self.save(model)

        self._ws.append_event({
            "event": "mental_model_reconstructed",
            "source": "mental_model_manager",
            "num_hypotheses": len(model.active_hypotheses) + len(model.competing_hypotheses),
            "num_mechanisms": len(model.inferred_mechanisms),
            "num_causal": len(model.causal_relationships),
            "num_ambiguities": len(model.unresolved_ambiguity),
        })

        return model

    # ------------------------------------------------------------------
    # Evidence summary preparation
    # ------------------------------------------------------------------

    def _prepare_evidence_summary(self, artifacts: ResearchArtifacts) -> str:
        """Compile a concise evidence summary from the artifact record."""
        sections: list[str] = []

        # Validated evidence
        validated_obs = [
            o for o in artifacts.observations if o.observation_id in artifacts.validated_evidence
        ]
        if validated_obs:
            lines = ["### Validated Evidence"]
            for obs in validated_obs:
                vals = ", ".join(f"{k}={v}" for k, v in obs.numerical_values.items())
                lines.append(f"- [{obs.observation_id}] (from {obs.source_job_id}): "
                             f"{obs.description}" + (f" ({vals})" if vals else ""))
            sections.append("\n".join(lines))

        # Unvalidated observations
        unvalidated = [
            o for o in artifacts.observations if o.observation_id not in artifacts.validated_evidence
        ]
        if unvalidated:
            lines = ["### Unvalidated Observations"]
            for obs in unvalidated[-20:]:  # limit to most recent 20
                vals = ", ".join(f"{k}={v}" for k, v in obs.numerical_values.items())
                lines.append(f"- [{obs.observation_id}] (from {obs.source_job_id}): "
                             f"{obs.description}" + (f" ({vals})" if vals else ""))
            if len(unvalidated) > 20:
                lines.append(f"  ... and {len(unvalidated) - 20} more")
            sections.append("\n".join(lines))

        # Current claims
        if artifacts.claims:
            lines = ["### Current Scientific Claims"]
            for c in artifacts.claims:
                lines.append(f"- [{c.status.upper()}] {c.statement} "
                             f"(confidence={c.confidence:.2f}, "
                             f"evidence={len(c.supporting_evidence)} items)")
                if c.status == "rejected" and c.rejection_reason:
                    lines.append(f"  Rejected: {c.rejection_reason}")
            sections.append("\n".join(lines))

        # Failed experiments
        if artifacts.failed_experiments:
            lines = ["### Failed Experiments"]
            for exp in artifacts.failed_experiments[-10:]:
                lines.append(f"- {exp.job_id} ({exp.skill_used}): {exp.failure_diagnosis or 'unknown'}")
            sections.append("\n".join(lines))

        # Open questions
        if artifacts.open_questions:
            lines = ["### Open Questions"]
            for q in artifacts.open_questions:
                lines.append(f"- {q}")
            sections.append("\n".join(lines))

        # Auditor findings
        active_findings = [f for f in artifacts.auditor_findings if f.severity != "info"]
        if active_findings:
            lines = ["### Active Auditor Findings"]
            for af in active_findings[-5:]:
                lines.append(f"- [{af.severity.upper()}] {af.finding} → {af.outcome.value}")
            sections.append("\n".join(lines))

        return "\n\n".join(sections) if sections else "(No evidence accumulated yet.)"

    def _build_reconstruction_prompt(
        self,
        artifacts: ResearchArtifacts,
        evidence_summary: str,
    ) -> str:
        """Fill in the reconstruction prompt template."""
        return _RECONSTRUCTION_PROMPT.format(
            objective=artifacts.initial_objective,
            current_goal=artifacts.current_goal,
            evidence_summary=evidence_summary,
        )

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    def _parse_model_response(self, raw: str) -> ScientificMentalModel:
        """Parse the LLM's JSON response into a ScientificMentalModel.

        Handles markdown code fences and minor formatting variations.
        Falls back to an empty model if parsing fails completely.
        """
        text = raw.strip()

        # Strip markdown code fences if present
        if text.startswith("```"):
            lines = text.split("\n")
            # Remove first and last lines (```json and ```)
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines)

        try:
            data: dict[str, Any] = json.loads(text)
            return ScientificMentalModel.model_validate(data)
        except (json.JSONDecodeError, Exception) as exc:
            logger.warning(
                "Failed to parse mental model response: %s. "
                "Returning empty model.",
                exc,
            )
            return ScientificMentalModel()
