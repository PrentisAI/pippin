"""Experiment planning subsystem (Phase 6).

Provides the :class:`ExperimentPlanner` (LLM-driven decomposition of a
proposal into a DAG of computational jobs), a :class:`DependencyGraph` for
scheduling, and a :class:`PreflightVerifier` for pre-execution checks.
"""

from aurora.planning.dependency_graph import DependencyGraph
from aurora.planning.planner import ExperimentPlanner
from aurora.planning.preflight import PreflightVerifier

__all__ = [
    "DependencyGraph",
    "ExperimentPlanner",
    "PreflightVerifier",
]
