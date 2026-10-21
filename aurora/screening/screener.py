"""Idea screener — multi-axis evaluation of roundtable proposals.

The :class:`IdeaScreener` assigns a Novelty × Feasibility × Significance
score to each :class:`RoundtableProposal` using a single cheap LLM call
(routed through :data:`PipelineStage.IDEA_SCREENING`).  The composite
score follows the weighting scheme described in the paper:

    composite = novelty × 0.35  +  feasibility × 0.35  +  significance × 0.30
"""

from __future__ import annotations

import asyncio
import logging
from typing import Sequence

from aurora.config import PipelineStage
from aurora.llm.base import Message
from aurora.llm.router import LLMRouter
from aurora.models.roundtable import IdeaScore, RoundtableProposal

logger = logging.getLogger(__name__)

# Axis weights for the composite score.
_W_NOVELTY: float = 0.35
_W_FEASIBILITY: float = 0.35
_W_SIGNIFICANCE: float = 0.30

_DEFAULT_SCREENING_PROMPT = """\
You are an expert reviewer evaluating research proposals on three axes, \
each scored from 0 (worst) to 10 (best):

1. **Novelty** — Does the proposal address a genuinely open question or \
propose a new methodology?  Penalise incremental work or well-trodden ground.
2. **Feasibility** — Can the proposed methods realistically deliver results \
within typical compute budgets and timelines?  Consider code maturity, data \
availability, and methodological soundness.
3. **Significance** — If successful, would the outcomes advance the field \
meaningfully?  High significance means the results could change experimental \
practice or unlock new research directions.

Return a structured score with a brief explanation justifying each axis. \
Compute the composite score as:

    composite = novelty * 0.35 + feasibility * 0.35 + significance * 0.30
"""


class IdeaScreener:
    """Evaluate roundtable proposals on Novelty × Feasibility × Significance.

    Parameters
    ----------
    router:
        Shared LLM router — calls are placed on the
        :data:`PipelineStage.IDEA_SCREENING` stage (typically a cheap,
        fast model such as ``gpt-4o-mini``).
    system_prompt:
        Optional custom system prompt. When empty, uses the domain-agnostic
        default.
    """

    def __init__(self, router: LLMRouter, *, system_prompt: str = "") -> None:
        self._router = router
        self._system_prompt = system_prompt or _DEFAULT_SCREENING_PROMPT

    # ------------------------------------------------------------------
    # Single proposal
    # ------------------------------------------------------------------

    async def screen(self, proposal: RoundtableProposal, prior_work: str = "") -> IdeaScore:
        """Score a single proposal.

        Parameters
        ----------
        proposal:
            The roundtable proposal to evaluate.
        prior_work:
            The closest prior work found in the scholarly indexes (a Markdown section), if any.

        Returns
        -------
        IdeaScore
            Structured score with per-axis values, composite, and
            explanation.  The ``composite`` field is recalculated
            server-side to guarantee the canonical weighting.
        """
        user_content = _format_proposal(proposal) + (f"\n\n{prior_work}" if prior_work else "")
        messages = [Message.user(user_content)]

        logger.debug(
            "Screening proposal %s (%s)",
            proposal.proposal_id,
            proposal.title,
        )

        score: IdeaScore = await self._router.generate_structured(
            PipelineStage.IDEA_SCREENING,
            messages,
            IdeaScore,
            system_prompt=self._system_prompt,
            temperature=0.3,
        )

        # Enforce canonical composite regardless of what the LLM returned.
        score = score.model_copy(
            update={
                "composite": round(
                    score.novelty * _W_NOVELTY
                    + score.feasibility * _W_FEASIBILITY
                    + score.significance * _W_SIGNIFICANCE,
                    4,
                ),
            },
        )

        logger.info(
            "Proposal %s scored: N=%.1f  F=%.1f  S=%.1f  C=%.2f",
            proposal.proposal_id,
            score.novelty,
            score.feasibility,
            score.significance,
            score.composite,
        )

        return score

    # ------------------------------------------------------------------
    # Batch screening
    # ------------------------------------------------------------------

    async def screen_batch(
        self,
        proposals: Sequence[RoundtableProposal],
    ) -> list[tuple[RoundtableProposal, IdeaScore]]:
        """Screen multiple proposals concurrently and rank by composite.

        Parameters
        ----------
        proposals:
            Proposals to evaluate (order does not matter).

        Returns
        -------
        list[tuple[RoundtableProposal, IdeaScore]]
            ``(proposal, score)`` pairs sorted **descending** by
            ``score.composite``.
        """
        if not proposals:
            return []

        logger.info("Batch-screening %d proposals", len(proposals))

        scores: list[IdeaScore] = await asyncio.gather(
            *(self.screen(p) for p in proposals),
        )

        paired: list[tuple[RoundtableProposal, IdeaScore]] = list(
            zip(proposals, scores),
        )
        paired.sort(key=lambda pair: pair[1].composite, reverse=True)

        logger.info(
            "Batch screening complete — top proposal: %s (%.2f)",
            paired[0][0].proposal_id,
            paired[0][1].composite,
        )

        return paired


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _format_proposal(proposal: RoundtableProposal) -> str:
    """Render a proposal into a prompt-friendly text block."""
    sections: list[str] = [
        f"# Proposal: {proposal.title}",
        f"**ID:** {proposal.proposal_id}",
        "",
        "## Description",
        proposal.description,
    ]

    if proposal.research_questions:
        sections.append("\n## Research Questions")
        for i, q in enumerate(proposal.research_questions, 1):
            sections.append(f"{i}. {q}")

    if proposal.proposed_methods:
        sections.append("\n## Proposed Methods")
        for m in proposal.proposed_methods:
            sections.append(f"- {m}")

    if proposal.expected_outcomes:
        sections.append("\n## Expected Outcomes")
        for o in proposal.expected_outcomes:
            sections.append(f"- {o}")

    return "\n".join(sections)
