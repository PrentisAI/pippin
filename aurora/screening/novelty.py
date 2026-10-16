"""Novelty checker — literature-backed novelty assessment.

The :class:`NoveltyChecker` combines a literature search (via any
:class:`Tool` registered under the ``"literature"`` name) with an LLM
comparison pass to quantify how novel a proposal is relative to published
work.  The output :class:`NoveltyResult` captures the score, a list of
similar works found, the key differentiators, and a narrative explanation.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field

from aurora.config import PipelineStage
from aurora.llm.base import Message
from aurora.llm.router import LLMRouter
from aurora.models.roundtable import RoundtableProposal
from aurora.tools.base import Tool

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Result model (new — not defined elsewhere in the codebase)
# ------------------------------------------------------------------

class NoveltyResult(BaseModel):
    """Structured output of a novelty assessment.

    Attributes
    ----------
    score:
        Novelty score from 0 (fully redundant) to 10 (entirely new).
    similar_works:
        Published works that overlap with the proposal, each represented
        as a dict with at least ``title``, ``authors``, and ``overlap``
        (a short description of the overlap).
    differentiators:
        Concise statements of what makes the proposal distinct from each
        similar work.
    explanation:
        Free-text rationale tying the score, similar works, and
        differentiators together.
    """

    score: float = Field(ge=0.0, le=10.0, description="Novelty score 0–10.")
    similar_works: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "Published works that overlap with the proposal.  Each entry "
            "should contain at least 'title', 'authors', and 'overlap'."
        ),
    )
    differentiators: list[str] = Field(
        default_factory=list,
        description="Key aspects that distinguish the proposal from prior art.",
    )
    explanation: str = Field(
        default="",
        description="Narrative explanation of the novelty assessment.",
    )


# ------------------------------------------------------------------
# System prompt for the LLM comparison step
# ------------------------------------------------------------------

_NOVELTY_SYSTEM_PROMPT = """\
You are an expert scientific reviewer.  Given a research proposal and a set \
of related published papers retrieved from the literature, assess the \
proposal's novelty.

Score from 0 (the proposal duplicates existing work) to 10 (the proposal is \
entirely unprecedented).

For each similar work, note the specific overlap with the proposal.  Then \
identify concrete differentiators — new methods, new materials, new questions, \
or new computational approaches that go beyond the existing literature.

Return a structured JSON result with:
- score (float 0–10)
- similar_works (list of {title, authors, overlap})
- differentiators (list of strings)
- explanation (narrative string)
"""


class NoveltyChecker:
    """Literature-backed novelty assessment for roundtable proposals.

    Parameters
    ----------
    router:
        Shared LLM router.  The comparison pass runs on the
        :data:`PipelineStage.IDEA_SCREENING` stage.
    literature_tool:
        A :class:`Tool` (typically the ``"literature"`` tool) used to
        search for published work related to the proposal.
    max_lit_results:
        Maximum number of literature results to feed into the LLM
        comparison.
    """

    def __init__(
        self,
        router: LLMRouter,
        literature_tool: Tool,
        *,
        max_lit_results: int = 10,
    ) -> None:
        self._router = router
        self._lit_tool = literature_tool
        self._max_lit_results = max_lit_results

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def check_novelty(
        self,
        proposal: RoundtableProposal,
    ) -> NoveltyResult:
        """Assess the novelty of *proposal* against the literature.

        Workflow
        -------
        1. Build one or more search queries from the proposal's title,
           research questions, and methods.
        2. Execute the literature search tool.
        3. Format the retrieved papers and the proposal into a prompt.
        4. Ask the LLM (via :data:`PipelineStage.IDEA_SCREENING`) to
           produce a structured :class:`NoveltyResult`.

        Parameters
        ----------
        proposal:
            The roundtable proposal to check.

        Returns
        -------
        NoveltyResult
            Structured novelty assessment.
        """
        logger.info(
            "Checking novelty for proposal %s (%s)",
            proposal.proposal_id,
            proposal.title,
        )

        # 1. Search the literature -----------------------------------------
        papers = await self._search_literature(proposal)
        logger.debug("Literature search returned %d papers", len(papers))

        # 2. Build the comparison prompt -----------------------------------
        user_content = _build_comparison_prompt(proposal, papers)
        messages = [Message.user(user_content)]

        # 3. LLM comparison ------------------------------------------------
        result: NoveltyResult = await self._router.generate_structured(
            PipelineStage.IDEA_SCREENING,
            messages,
            NoveltyResult,
            system_prompt=_NOVELTY_SYSTEM_PROMPT,
            temperature=0.3,
        )

        logger.info(
            "Novelty assessment for %s: score=%.1f, %d similar works, "
            "%d differentiators",
            proposal.proposal_id,
            result.score,
            len(result.similar_works),
            len(result.differentiators),
        )

        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _search_literature(
        self,
        proposal: RoundtableProposal,
    ) -> list[dict[str, Any]]:
        """Execute literature queries derived from the proposal.

        Constructs a combined query from the proposal title, first
        research question (if any), and first proposed method (if any),
        then delegates to the literature tool.
        """
        query_parts: list[str] = [proposal.title]
        if proposal.research_questions:
            query_parts.append(proposal.research_questions[0])
        if proposal.proposed_methods:
            query_parts.append(proposal.proposed_methods[0])

        query = " ".join(query_parts)

        tool_result = await self._lit_tool.execute(
            {"query": query, "max_results": self._max_lit_results},
        )

        if not tool_result.success:
            logger.warning(
                "Literature search failed for proposal %s: %s",
                proposal.proposal_id,
                tool_result.error,
            )
            return []

        return tool_result.output.get("papers", [])

    @staticmethod
    def _format_papers(papers: list[dict[str, Any]]) -> str:
        """Render retrieved papers into a prompt-friendly block."""
        if not papers:
            return "(No related papers found in the literature search.)"

        lines: list[str] = []
        for i, paper in enumerate(papers, 1):
            title = paper.get("title", "Untitled")
            authors = paper.get("authors", "Unknown")
            year = paper.get("year", "N/A")
            abstract = paper.get("abstract", "")
            keywords = ", ".join(paper.get("keywords", []))

            lines.append(f"### Paper {i}: {title}")
            lines.append(f"**Authors:** {authors} ({year})")
            if keywords:
                lines.append(f"**Keywords:** {keywords}")
            if abstract:
                lines.append(f"**Abstract:** {abstract}")
            lines.append("")

        return "\n".join(lines)


# ----------------------------------------------------------------------
# Prompt construction
# ----------------------------------------------------------------------

def _build_comparison_prompt(
    proposal: RoundtableProposal,
    papers: list[dict[str, Any]],
) -> str:
    """Build the full user prompt for the novelty comparison LLM call."""
    sections: list[str] = [
        "# Proposal Under Review",
        f"**Title:** {proposal.title}",
        f"**Description:** {proposal.description}",
    ]

    if proposal.research_questions:
        sections.append("\n**Research Questions:**")
        for q in proposal.research_questions:
            sections.append(f"- {q}")

    if proposal.proposed_methods:
        sections.append("\n**Proposed Methods:**")
        for m in proposal.proposed_methods:
            sections.append(f"- {m}")

    if proposal.expected_outcomes:
        sections.append("\n**Expected Outcomes:**")
        for o in proposal.expected_outcomes:
            sections.append(f"- {o}")

    sections.append("\n---\n")
    sections.append("# Related Published Work")
    sections.append(NoveltyChecker._format_papers(papers))

    sections.append("\n---\n")
    sections.append(
        "Assess the novelty of the proposal relative to the papers above. "
        "Return a structured NoveltyResult."
    )

    return "\n".join(sections)
