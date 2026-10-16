"""Materials-science domain skills (Table 1 of the paper).

Seven skills covering the end-to-end computational workflow for
Cu–Zn alloy surface segregation under CO₂ hydrogenation conditions.
"""

from __future__ import annotations

from aurora.skills.materials.compute_orchestration import ComputeOrchestrationSkill
from aurora.skills.materials.convergence_analysis import ConvergenceAnalysisSkill
from aurora.skills.materials.scientific_visualization import (
    ScientificVisualizationSkill,
)
from aurora.skills.materials.segregation_analysis import SegregationAnalysisSkill
from aurora.skills.materials.simulation_diagnosis import SimulationDiagnosisSkill
from aurora.skills.materials.statistical_validation import (
    StatisticalValidationSkill,
)
from aurora.skills.materials.structure_construction import (
    StructureConstructionSkill,
)

ALL_SKILLS = [
    StructureConstructionSkill,
    ConvergenceAnalysisSkill,
    SegregationAnalysisSkill,
    SimulationDiagnosisSkill,
    StatisticalValidationSkill,
    ScientificVisualizationSkill,
    ComputeOrchestrationSkill,
]

__all__ = [
    "ALL_SKILLS",
    "ComputeOrchestrationSkill",
    "ConvergenceAnalysisSkill",
    "ScientificVisualizationSkill",
    "SegregationAnalysisSkill",
    "SimulationDiagnosisSkill",
    "StatisticalValidationSkill",
    "StructureConstructionSkill",
]
