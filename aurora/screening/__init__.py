"""Phase 5 — Idea screening and novelty assessment.

The screening phase evaluates roundtable proposals on three axes (Novelty,
Feasibility, Significance) and cross-references them against existing
literature to quantify their novelty delta.

Public API
----------
IdeaScreener
    Multi-axis scoring of roundtable proposals via a lightweight LLM pass.
NoveltyChecker
    Literature-backed novelty assessment combining search results with an
    LLM comparison.
"""

from aurora.screening.novelty import NoveltyChecker, NoveltyResult
from aurora.screening.screener import IdeaScreener

__all__ = [
    "IdeaScreener",
    "NoveltyChecker",
    "NoveltyResult",
]
