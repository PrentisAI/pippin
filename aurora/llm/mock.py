"""Mock LLM provider for testing.

Provides deterministic, configurable responses without any external API
calls.  All calls are recorded for assertion in tests.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, TypeVar

from pydantic import BaseModel

from aurora.llm.base import (
    LLMProvider,
    LLMResponse,
    LLMResponseParseError,
    Message,
    TokenUsage,
    UsageTracker,
)

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


@dataclass
class RecordedCall:
    """A single recorded LLM call for test assertions."""

    method: str  # "generate" or "generate_structured"
    messages: list[Message]
    model: str | None
    kwargs: dict[str, Any]
    response: str


# Text answer for the mental-model reconstruction of the scripted study
_MENTAL_MODEL_TEXT = json.dumps({
    "active_hypotheses": [
        {
            "hypothesis_id": "h1",
            "statement": "Zn preferentially segregates to the Cu(111) surface at low coverage",
            "confidence": 0.65,
            "supporting_evidence": ["obs_seg"],
            "contradicting_evidence": [],
        },
    ],
    "competing_hypotheses": [],
    "rejected_hypotheses": [],
    "inferred_mechanisms": [
        "Size mismatch (Zn > Cu) provides thermodynamic driving force for surface segregation",
    ],
    "causal_relationships": [
        {
            "cause": "Zn atom size mismatch with Cu lattice",
            "effect": "Surface segregation preference",
            "mechanism": "Strain relief at free surface",
            "confidence": 0.6,
            "supporting_evidence": ["obs_seg"],
        },
    ],
    "current_rationale": "Zn segregation to Cu(111) is driven by size mismatch strain relief.",
    "unresolved_ambiguity": ["Coverage dependence not yet established"],
    "expected_outcomes": {
        "Zn segregation energy at higher coverage": "ΔE_seg becomes less negative with increasing Zn coverage",
        "NequIP cross-validation": "ΔΔE_seg < 0.5 eV between DFT and NequIP",
    },
    "schema_version": "aurora_mental_model_v0",
})


class MockProvider(LLMProvider):
    """Deterministic mock LLM provider for testing.

    Parameters
    ----------
    responses:
        A mapping of keyword → response content.  When generating, the
        provider searches the last user message for each keyword (in
        insertion order) and returns the first match.
    default_response:
        Fallback response when no keyword matches.
    structured_defaults:
        Mapping of Pydantic model name → dict of default field values.
        Used by ``generate_structured`` to build instances.
    model_id:
        Model identifier reported in responses.
    usage_tracker:
        Shared usage tracker instance.
    """

    provider_name = "mock"

    # Built-in mock defaults for known AURORA schemas.
    # These produce realistic-looking data so that the pipeline executes
    # all phases (roundtable → … → writing) with non-trivial content.
    _BUILTIN_DEFAULTS: dict[str, dict[str, Any]] = {
        "_LLMPlanResponse": {
            "reasoning": "Mock plan: structure → convergence → segregation analysis chain.",
            "jobs": [
                {
                    "name": "build_slab",
                    "skill_name": "structure_construction",
                    "parameters": {
                        "material": "Cu",
                        "surface": "(111)",
                        "layers": 6,
                        "vacuum": 15.0,
                    },
                    "expected_outputs": ["POSCAR_slab.vasp"],
                    "resource_requirements": {"num_gpus": 0},
                    "depends_on": [],
                },
                {
                    "name": "converge_encut",
                    "skill_name": "convergence_analysis",
                    "parameters": {
                        "facet": "111",
                        "ecutwfc": 60,
                        "tolerance_mev_per_atom": 1.0,
                        "kpoint_grids": [[2, 2, 1], [3, 3, 1], [4, 4, 1]],
                    },
                    "expected_outputs": ["convergence_encut.json"],
                    "resource_requirements": {"num_gpus": 1},
                    "depends_on": ["build_slab"],
                },
                {
                    "name": "segregation_calc",
                    "skill_name": "segregation_analysis",
                    "parameters": {
                        "e_seg_ads": -125.30,
                        "e_sub_ads": -125.00,
                        "e_seg_clean": -120.50,
                        "e_sub_clean": -120.20,
                        "solute": "Zn",
                        "host": "Cu",
                        "surface": "(111)",
                    },
                    "expected_outputs": ["segregation_energies.json"],
                    "resource_requirements": {"num_gpus": 1},
                    "depends_on": ["converge_encut"],
                },
            ],
        },
        "ChairJudgment": {
            "converged": True,
            "reason": "Seats agree on Cu-Zn surface segregation as a promising direction.",
            "next_speakers": [],
            "key_agreements": [
                "Zn segregation to Cu(111) surface is energetically favorable",
                "DFT+U corrections needed for accurate energetics",
            ],
            "key_disagreements": [],
            "proposal_title": "Surface Segregation Energetics of Zn in Cu(111)",
            "proposal_description": (
                "Systematic DFT investigation of Zn segregation to Cu(111) "
                "surface layers and its effect on CO adsorption for methanol "
                "synthesis catalyst design."
            ),
            "research_questions": [
                "What is the segregation energy profile of Zn across Cu(111) surface layers?",
                "How does Zn surface concentration affect CO binding energy?",
                "Is there an optimal Zn coverage for selective CO activation?",
            ],
            "proposed_methods": [
                "PBE+D3 slab calculations with 6-layer Cu(111) slabs",
                "Systematic Zn substitution at layers 1-3",
                "CO adsorption energy mapping on Zn-decorated surfaces",
            ],
            "expected_outcomes": [
                "Segregation energy profile as function of layer depth",
                "CO adsorption energy vs. Zn surface concentration",
                "Optimal Zn coverage recommendation",
            ],
        },
        "ItemNotes": {
            "threads": [{"raised_by": "p02", "kind": "concern", "about": "p01",
                         "gist": "Clean-surface slabs ignore adsorbate coverage.",
                         "outcome": "accepted", "outcome_note": "Coverage series added."}],
            "summary": "p01's segregation plan was discussed; a coverage control was added.",
        },
        "Decision": {
            "chosen": "p01",
            "direction": "DFT segregation energetics of Zn in Cu(111) with and without adsorbates.",
            "rationale": "Concerns were answered and the plan is feasible.",
            "accepted_changes": ["Add a coverage series"],
            "not_chosen": [],
            "open_concerns": [],
            "backlog": [],
        },
        "Selection": {
            "chosen": "A",
            "rationale": "Idea A tests a concrete, falsifiable mechanism with a feasible budget.",
            "not_chosen": ["B: less directly tied to the objective"],
        },
        "FinalProposal": {
            "title": "Surface Segregation Energetics of Zn in Cu(111)",
            "problem": "How do CO2-hydrogenation intermediates change the segregation of Zn in Cu(111)?",
            "hypothesis": "Strongly bound intermediates reverse the clean-surface Zn segregation preference.",
            "method": "PBE+D3 slab calculations with Zn substituted at layers 1-3, with and without adsorbates.",
            "alternatives_considered": "Clean-surface segregation only; rejected as it ignores reaction conditions.",
            "falsification_check": "No sign change of the segregation energy for any adsorbate.",
            "expected_outcomes": ["Segregation energy profile with and without adsorbates"],
            "unresolved_disagreements": [],
        },
        "SeatResponse": {
            "position": (
                "Zn segregation to Cu(111) is a promising research direction "
                "for methanol synthesis catalyst design."
            ),
            "key_points": [
                "Cu-Zn alloys are industrial methanol synthesis catalysts",
                "Surface segregation controls active site composition",
                "DFT can predict segregation energetics accurately",
            ],
            "evidence_cited": [],
            "unverified_claims": [
                "(my claim, unverified) Zn prefers subsurface sites in Cu(111)",
            ],
            "responses_to_others": [],
            "proposed_experiments": [
                "6-layer Cu(111) slab with Zn substitution at each layer",
            ],
            "confidence": 0.75,
        },
        "ManuscriptPlan": {
            "title": (
                "First-Principles Investigation of Zn Segregation in Cu(111) "
                "and Its Implications for Methanol Synthesis"
            ),
            "abstract_draft": (
                "We present a systematic density functional theory study of Zn "
                "segregation energetics in Cu(111) surfaces. Our calculations "
                "reveal favorable segregation of Zn to the topmost surface layer "
                "with a segregation energy of approximately -0.3 eV. The presence "
                "of surface Zn modifies CO adsorption energetics, suggesting an "
                "optimal surface Zn coverage of 25% for balanced CO activation "
                "and methanol selectivity."
            ),
            "sections": [
                {"name": "Introduction", "outline": "Cu-Zn catalysts for methanol synthesis; motivation for DFT study", "status": "planned"},
                {"name": "Computational Methods", "outline": "DFT setup, slab models, convergence parameters", "status": "planned"},
                {"name": "Results", "outline": "Segregation energies, CO adsorption, coverage effects", "status": "planned"},
                {"name": "Discussion", "outline": "Implications for catalyst design, comparison with experiment", "status": "planned"},
                {"name": "Conclusion", "outline": "Key findings and outlook", "status": "planned"},
            ],
            "figures_needed": [
                "Segregation energy vs. layer depth",
                "CO adsorption energy vs. Zn coverage",
                "Optimized slab structures",
            ],
            "tables_needed": [
                "Convergence test results",
                "Segregation energies for each layer",
            ],
            "knowledge_gaps": [],
            "followup_needed": False,
            "followup_questions": [],
            "completeness": 0.8,
        },
        "SectionDraft": {
            "section_name": "Results",
            "content": (
                "Our DFT calculations reveal that Zn preferentially segregates "
                "to the topmost layer of Cu(111) with a segregation energy of "
                "-0.31 eV. The second-layer segregation energy is -0.08 eV, "
                "while deeper layers show negligible driving force for segregation. "
                "These results are consistent with the larger atomic radius of Zn "
                "compared to Cu, which favors surface site occupation to relieve "
                "elastic strain."
            ),
            "evidence_cited": ["obs_001", "obs_002"],
            "figures_referenced": ["fig_segregation_profile"],
            "confidence": 0.75,
            "gaps_in_section": [],
        },
        "AnalysisResult": {
            "findings": [
                {"description": "Zn segregation to Cu(111) surface is energetically favorable (-0.31 eV)", "evidence": "obs_001"},
                {"description": "Convergence achieved at ENCUT=500 eV with 0.005 eV accuracy", "evidence": "obs_002"},
                {"description": "CO adsorption weakened by 0.15 eV on Zn-decorated surfaces", "evidence": "obs_003"},
            ],
            "statistical_summary": {
                "n_experiments": 3,
                "n_observations": 6,
                "mean_confidence": 0.75,
            },
            "quality_flags": [
                "convergence_ok",
                "segregation_energy_within_threshold",
            ],
            "recommended_visualizations": [
                {"type": "line_plot", "data_source": "segregation_energies.json", "title": "Segregation Energy Profile"},
                {"type": "convergence_plot", "data_source": "convergence_encut.json", "title": "ENCUT Convergence"},
            ],
            "claims_supported": [
                "Zn prefers surface sites in Cu(111)",
            ],
            "claims_contradicted": [],
            "followup_needed": [],
            "overall_confidence": 0.75,
            "publication_ready": True,
        },
        "ResultAssessment": {
            "success": True,
            "observations": [
                {
                    "description": "Calculation completed successfully with converged SCF",
                    "numerical_values": {"total_energy": -125.3, "scf_cycles": 15},
                },
            ],
            "anomalies": [],
            "next_action": "continue",
            "reasoning": "Job completed successfully, results within expected ranges.",
        },
    }

    def __init__(
        self,
        responses: dict[str, str] | None = None,
        default_response: str = "This is a mock response.",
        structured_defaults: dict[str, dict[str, Any]] | None = None,
        model_id: str = "mock-model",
        usage_tracker: UsageTracker | None = None,
    ) -> None:
        super().__init__(usage_tracker=usage_tracker)
        self._responses: dict[str, str] = responses or {"scientific reasoning engine": _MENTAL_MODEL_TEXT}
        self._default_response = default_response
        # Merge builtin defaults with user overrides (user takes precedence)
        self._structured_defaults: dict[str, dict[str, Any]] = {
            **self._BUILTIN_DEFAULTS,
            **(structured_defaults or {}),
        }
        self._model_id = model_id
        self.call_history: list[RecordedCall] = []

    # -- helpers --------------------------------------------------------------

    def _find_response(self, messages: list[Message]) -> str:
        """Find a matching canned response based on the last user message."""
        last_user_msg = ""
        for m in reversed(messages):
            if m.role.value == "user":
                last_user_msg = m.content
                break

        for keyword, response in self._responses.items():
            if keyword.lower() in last_user_msg.lower():
                return response

        return self._default_response

    def _make_usage(self, prompt: str, response: str) -> TokenUsage:
        """Approximate token counts (4 chars ≈ 1 token)."""
        prompt_tokens = max(1, len(prompt) // 4)
        completion_tokens = max(1, len(response) // 4)
        return TokenUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        )

    # -- Core interface -------------------------------------------------------

    async def generate(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        system_prompt: str | None = None,
    ) -> LLMResponse:
        model = model or self._model_id
        content = self._find_response(messages)

        prompt_text = " ".join(m.content for m in messages)
        usage = self._make_usage(prompt_text, content)
        self._record_usage(model, usage)

        self.call_history.append(
            RecordedCall(
                method="generate",
                messages=messages,
                model=model,
                kwargs={"temperature": temperature, "max_tokens": max_tokens},
                response=content,
            )
        )

        return LLMResponse(
            content=content,
            model_id=model,
            usage=usage,
            finish_reason="stop",
        )

    async def generate_structured(
        self,
        messages: list[Message],
        schema: type[T],
        *,
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        system_prompt: str | None = None,
    ) -> T:
        model = model or self._model_id
        schema_name = schema.__name__

        # Strategy 1: Try keyword-based JSON matching (from set_responses)
        if getattr(self, "_keyword_json_mode", False):
            raw = self._find_response(messages)
            try:
                instance = self._parse_structured_response(raw, schema)
                response_json = instance.model_dump_json()
                prompt_text = " ".join(m.content for m in messages)
                usage = self._make_usage(prompt_text, response_json)
                self._record_usage(model, usage)
                self.call_history.append(RecordedCall(
                    method="generate_structured",
                    messages=messages, model=model,
                    kwargs={"schema": schema_name, "temperature": temperature},
                    response=response_json,
                ))
                return instance
            except Exception:
                pass  # Fall through to defaults

        # Strategy 2: Check if there is a canned structured default for this schema
        defaults = self._structured_defaults.get(schema_name, {})

        # Try to build the schema instance from defaults
        # Use the schema's own defaults for any field not in our overrides
        try:
            instance = schema.model_validate(defaults)
        except Exception:
            # Last resort: try to construct with minimal fields
            try:
                instance = schema.model_construct(**defaults)
            except Exception as exc:
                raise LLMResponseParseError(
                    f"MockProvider cannot construct {schema_name} from defaults: {exc}",
                    raw_content=json.dumps(defaults),
                ) from exc

        response_json = instance.model_dump_json()

        prompt_text = " ".join(m.content for m in messages)
        usage = self._make_usage(prompt_text, response_json)
        self._record_usage(model, usage)

        self.call_history.append(
            RecordedCall(
                method="generate_structured",
                messages=messages,
                model=model,
                kwargs={"schema": schema_name, "temperature": temperature},
                response=response_json,
            )
        )

        return instance

    async def close(self) -> None:
        """No-op for mock provider."""

    # -- test helpers ---------------------------------------------------------

    @property
    def call_count(self) -> int:
        """Total number of calls made."""
        return len(self.call_history)

    def calls_for(self, method: str = "generate") -> list[RecordedCall]:
        """Return calls filtered by method name."""
        return [c for c in self.call_history if c.method == method]

    def reset(self) -> None:
        """Clear call history and usage tracker."""
        self.call_history.clear()
        self._usage_tracker.reset()

    def last_call(self) -> RecordedCall | None:
        """Return the most recent call, or ``None``."""
        return self.call_history[-1] if self.call_history else None

    def set_responses(self, responses: dict[str, str]) -> None:
        """Replace the keyword → response mapping.

        This also enables keyword-based matching in ``generate_structured``:
        if a canned response is valid JSON for the target schema, it is used
        directly instead of the ``structured_defaults`` fallback.
        """
        self._responses = responses
        self._keyword_json_mode = True

    def set_default_response(self, response: str) -> None:
        """Replace the default fallback response."""
        self._default_response = response
