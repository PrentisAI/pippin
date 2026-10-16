"""Deterministic mock LLM for fully offline demo runs.

This module provides canned responses for every pipeline stage so that
``python -m aurora run --config examples/simple_demo/config.yaml``
can complete end-to-end without any API keys.

Usage::

    from examples.simple_demo.mock_llm import create_demo_mock_provider
    provider = create_demo_mock_provider()
"""

from __future__ import annotations

import json
from aurora.llm.mock import MockProvider


# ---------------------------------------------------------------------------
# Canned responses by stage/keyword
# ---------------------------------------------------------------------------

_ROUNDTABLE_SEAT_RESPONSE = json.dumps({
    "position": "We should use DFT with PBE+D3 to compute Zn segregation energies "
                "on Cu(111) at multiple coverages, then cross-validate with NequIP.",
    "key_points": [
        "Cu(111) is the most stable low-index facet — start there",
        "PBE+D3 captures van der Waals interactions relevant to surface chemistry",
        "Coverage range 0.05–0.30 spans dilute to concentrated alloy limit",
        "NequIP cross-check ensures DFT results are not functional-dependent artifacts",
    ],
    "evidence_cited": [],
    "unverified_claims": [
        "(my claim, unverified) Zn prefers subsurface at low coverage on Cu(111)",
    ],
    "responses_to_others": [],
    "proposed_experiments": [
        "Slab convergence: k-points and vacuum thickness",
        "Zn segregation energy at 6 coverages",
        "CO adsorption on Zn-substituted surface",
    ],
})

_CHAIR_CONVERGENCE_RESPONSE = json.dumps({
    "converged": True,
    "reason": "Both seats agree on DFT+MLIP methodology for Cu(111) Zn segregation. "
              "Consensus on coverage range and cross-validation strategy.",
    "next_speakers": [],
    "synthesis": "Study Zn segregation on Cu(111) using DFT (PBE+D3) at coverages "
                 "0.05–0.30, cross-validate with NequIP, assess CO adsorption impact.",
    "key_agreements": [
        "Use DFT with PBE+D3 functional",
        "Focus on Cu(111) facet first",
        "Coverage range 0.05–0.30",
        "NequIP cross-validation required",
    ],
    "key_disagreements": [],
})

_CHAIR_SYNTHESIS_RESPONSE = json.dumps({
    "title": "Surface Segregation of Zn in Cu(111): DFT and MLIP Study for Methanol Synthesis",
    "description": "Systematic DFT study of Zn segregation energetics on Cu(111) at coverages "
                   "0.05–0.30, cross-validated with NequIP MLIP, with implications for "
                   "methanol synthesis catalyst design.",
    "research_questions": [
        "What is the Zn segregation energy on Cu(111) as a function of coverage?",
        "Does Zn prefer surface or subsurface sites?",
        "How does Zn segregation affect CO binding energy?",
    ],
    "proposed_methods": [
        "DFT (Quantum ESPRESSO, PBE+D3, PAW pseudopotentials)",
        "4-layer Cu(111) slab, 3×3 supercell",
        "NequIP MLIP for cross-validation",
        "CO adsorption on top/bridge/hollow sites",
    ],
    "expected_outcomes": [
        "ΔE_seg(x) curve showing coverage dependence",
        "Surface vs subsurface preference map",
        "ΔΔE_seg < 0.5 eV between DFT and NequIP",
        "CO binding energy shift due to Zn presence",
    ],
})

_SCREENING_RESPONSE = json.dumps({
    "novelty": 7.5,
    "feasibility": 8.0,
    "significance": 7.0,
    "composite": 7.5,
    "explanation": "The systematic coverage-dependent study with MLIP cross-validation "
                   "is novel. Computationally feasible with standard DFT. Significant "
                   "for methanol synthesis catalyst understanding.",
})

_PLANNING_RESPONSE = json.dumps({
    "jobs": [
        {
            "job_id": "job_structure",
            "skill_name": "structure_construction",
            "parameters": {
                "element": "Cu",
                "facet": "111",
                "supercell": [3, 3, 1],
                "layers": 4,
                "dopant": "Zn",
                "coverage": 0.11,
            },
            "expected_outputs": ["structure.json"],
            "dependencies": [],
        },
        {
            "job_id": "job_convergence",
            "skill_name": "convergence_analysis",
            "parameters": {
                "facet": "111",
                "k_points_range": [[2, 2, 1], [4, 4, 1], [6, 6, 1]],
                "ecutwfc_range": [40, 50, 60, 70],
            },
            "expected_outputs": ["convergence.json"],
            "dependencies": ["job_structure"],
        },
        {
            "job_id": "job_segregation",
            "skill_name": "segregation_analysis",
            "parameters": {
                "e_seg_ads": -234.56,
                "e_sub_ads": -234.33,
                "e_seg_clean": -230.12,
                "e_sub_clean": -230.10,
            },
            "expected_outputs": ["segregation.json"],
            "dependencies": ["job_convergence"],
        },
    ],
    "rationale": "Three-stage plan: build structure, verify convergence, compute segregation.",
})

_RESEARCHER_ASSESSMENT = json.dumps({
    "success": True,
    "observations": [
        {
            "description": "Zn segregation energy on Cu(111) at 11% coverage: ΔE_seg = -0.23 eV (exothermic, surface preferred)",
            "numerical_values": {"delta_E_seg_eV": -0.23, "coverage": 0.11},
        },
    ],
    "anomalies": [],
    "next_action": "continue",
    "reasoning": "Calculation converged normally. ΔE_seg is within expected range for Cu-Zn.",
})

_ANALYSIS_RESPONSE = json.dumps({
    "summary": "Zn shows moderate surface segregation preference on Cu(111) with "
               "ΔE_seg = -0.23 eV at 11% coverage, consistent with experimental trends.",
    "findings": [
        {
            "finding_id": "f1",
            "category": "observation",
            "description": "ΔE_seg = -0.23 eV indicates Zn prefers the Cu(111) surface over bulk at 11% coverage",
            "evidence": ["obs_struct", "obs_seg"],
            "numerical_results": {"delta_E_seg": -0.23},
            "confidence": 0.75,
            "needs_validation": True,
        },
        {
            "finding_id": "f2",
            "category": "claim",
            "description": "Zn surface segregation is thermodynamically favorable on Cu(111) at low coverage",
            "evidence": ["obs_seg"],
            "numerical_results": {"delta_E_seg": -0.23},
            "confidence": 0.65,
            "needs_validation": True,
        },
    ],
    "proposed_claims": [
        {"statement": "Zn exhibits moderate surface segregation on Cu(111)", "confidence": 0.65},
    ],
    "open_questions": ["Coverage dependence beyond 11%?", "What about Cu(100) and Cu(110)?"],
    "validation_recommendations": ["Cross-validate with NequIP MLIP", "Test higher coverages"],
    "quality_assessment": "preliminary",
})

# Writer plan response (matches ManuscriptPlan schema)
_WRITER_PLAN_RESPONSE = json.dumps({
    "title": "Surface Segregation of Zn in Cu(111): A First-Principles Study",
    "abstract_draft": "We investigate the surface segregation of Zn atoms in Cu(111) using "
                      "density functional theory with the PBE+D3 functional. At 11% Zn coverage, "
                      "we find a segregation energy of ΔE_seg = -0.23 eV, indicating a moderate "
                      "thermodynamic preference for Zn to occupy surface sites.",
    "sections": [
        {"name": "Introduction", "outline": "Background on Cu/ZnO catalysts", "status": "planned"},
        {"name": "Computational Methods", "outline": "DFT setup, slab model, convergence", "status": "planned"},
        {"name": "Results", "outline": "Segregation energies, CO binding", "status": "planned"},
        {"name": "Discussion", "outline": "Implications for catalysis", "status": "planned"},
        {"name": "Conclusion", "outline": "Summary and outlook", "status": "planned"},
    ],
    "figures_needed": ["Segregation energy vs coverage", "Slab model diagram"],
    "tables_needed": ["Convergence data", "Segregation energies"],
    "knowledge_gaps": ["MLIP cross-validation", "Other facets"],
    "followup_needed": False,
    "followup_questions": [],
    "completeness": 0.65,
})

# Writer section response (matches SectionDraft schema)
_WRITER_SECTION_RESPONSE = json.dumps({
    "section_name": "Section",
    "content": "Cu/ZnO catalysts are widely used for industrial methanol synthesis via "
               "CO₂ hydrogenation. Understanding the atomic-level structure of these "
               "catalysts, particularly Zn segregation to Cu surfaces, is crucial for "
               "rational catalyst design. In this work, we present a systematic density "
               "functional theory (DFT) study of Zn segregation energetics on the Cu(111) "
               "surface. Our calculations employ the PBE+D3 functional with PAW "
               "pseudopotentials and a 3×3×4 slab model. We find a segregation energy "
               "of ΔE_seg = -0.23 eV at 11% Zn coverage, indicating a moderate "
               "thermodynamic preference for Zn to occupy surface sites rather than "
               "bulk positions. This result is consistent with experimental observations "
               "of Zn enrichment at Cu/ZnO interfaces under reducing conditions.",
    "evidence_cited": ["obs_struct", "obs_seg"],
    "figures_referenced": ["fig_seg_energy"],
    "confidence": 0.70,
    "gaps_in_section": ["MLIP cross-validation needed"],
})

_AUDITOR_TRIAGE_RESPONSE = json.dumps({
    "severity": "info",
    "needs_deep_audit": False,
    "finding": "Calculation appears nominal. No anomalies detected.",
    "recommendation": "continue",
})

# Mental model reconstruction
_MENTAL_MODEL_RESPONSE = json.dumps({
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


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_demo_mock_provider() -> MockProvider:
    """Create a MockProvider with deterministic canned responses.

    The returned provider responds to any prompt with the appropriate
    canned response based on keyword matching.
    """
    provider = MockProvider()

    # Register keyword-based responses.
    # ORDER MATTERS — first match wins, so use specific keywords before
    # generic ones (e.g. "IdeaScore" before "proposal").
    responses: dict[str, str] = {
        # Screening — match before generic "proposal" / "Segregation"
        "IdeaScore": _SCREENING_RESPONSE,
        "novelty × feasibility": _SCREENING_RESPONSE,
        "screen this proposal": _SCREENING_RESPONSE,
        "Novelty": _SCREENING_RESPONSE,
        "# Proposal:": _SCREENING_RESPONSE,  # screener formats as "# Proposal: <title>"
        # Planning — specific keywords
        "decompose": _PLANNING_RESPONSE,
        "experiment plan": _PLANNING_RESPONSE,
        "skill_name": _PLANNING_RESPONSE,
        "ExperimentPlan": _PLANNING_RESPONSE,
        # Research assessment
        "ResultAssessment": _RESEARCHER_ASSESSMENT,
        "assess these results": _RESEARCHER_ASSESSMENT,
        "experiment results": _RESEARCHER_ASSESSMENT,
        # Analysis
        "AnalysisReport": _ANALYSIS_RESPONSE,
        "analyse all": _ANALYSIS_RESPONSE,
        "analyze all": _ANALYSIS_RESPONSE,
        # Writing — section drafts before plan (section prompt contains "Write the **")
        "Write the **": _WRITER_SECTION_RESPONSE,
        "SectionDraft": _WRITER_SECTION_RESPONSE,
        "Plan a scientific manuscript": _WRITER_PLAN_RESPONSE,
        "ManuscriptPlan": _WRITER_PLAN_RESPONSE,
        "manuscript": _WRITER_PLAN_RESPONSE,
        # Mental model — must be before "synthesise" since the prompt contains that word
        "scientific reasoning engine": _MENTAL_MODEL_RESPONSE,
        "scientific mental model": _MENTAL_MODEL_RESPONSE,
        "mental model": _MENTAL_MODEL_RESPONSE,
        "reconstruct": _MENTAL_MODEL_RESPONSE,
        "hypothes": _MENTAL_MODEL_RESPONSE,
        # Roundtable — more specific before generic
        "roundtable": _ROUNDTABLE_SEAT_RESPONSE,
        "your unique perspective": _ROUNDTABLE_SEAT_RESPONSE,
        "converged": _CHAIR_CONVERGENCE_RESPONSE,
        "convergence": _CHAIR_CONVERGENCE_RESPONSE,
        "synthesise": _CHAIR_SYNTHESIS_RESPONSE,
        "unified research proposal": _CHAIR_SYNTHESIS_RESPONSE,
        # Audit
        "triage": _AUDITOR_TRIAGE_RESPONSE,
        "audit": _AUDITOR_TRIAGE_RESPONSE,
    }

    provider.set_responses(responses)
    return provider
