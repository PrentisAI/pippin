"""Real literature search tool querying arXiv and Semantic Scholar.

Provides paper search and metadata retrieval from public academic APIs.
Falls back gracefully when network access is unavailable.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import urllib.parse
import urllib.request
from typing import Any
from xml.etree import ElementTree

from aurora.tools.base import Tool, ToolResult

logger = logging.getLogger(__name__)

# API endpoints
_ARXIV_API = "http://export.arxiv.org/api/query"
_SEMANTIC_SCHOLAR_API = "https://api.semanticscholar.org/graph/v1"

# Namespaces for arXiv Atom feed
_ARXIV_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
}


class RealLiteratureTool(Tool):
    """Literature search via arXiv and Semantic Scholar APIs.

    Supported operations
    --------------------
    ``search``
        Query one or both APIs and return matching papers.
    ``fetch_paper``
        Fetch detailed metadata for a specific paper by ID.
    """

    name = "literature"
    description = "Search scientific literature (arXiv + Semantic Scholar)"

    async def validate_params(self, params: dict[str, Any]) -> tuple[bool, str]:
        op = params.get("operation", "search")
        if op not in ("search", "fetch_paper"):
            return False, f"Unknown operation: {op!r}; choose from ('search', 'fetch_paper')"

        if op == "search" and "query" not in params:
            return False, "Missing required parameter: query"

        if op == "fetch_paper" and "paper_id" not in params:
            return False, "Missing required parameter: paper_id"

        return True, "Parameters OK."

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        valid, msg = await self.validate_params(params)
        if not valid:
            return ToolResult(success=False, error=msg)

        op = params.get("operation", "search")
        t0 = time.monotonic()
        try:
            if op == "search":
                result = await self._search(params)
            elif op == "fetch_paper":
                result = await self._fetch_paper(params)
            else:
                result = ToolResult(success=False, error=f"Unhandled: {op}")
            result.wall_time_seconds = time.monotonic() - t0
            return result
        except Exception as exc:
            logger.exception("Literature operation %r failed", op)
            return ToolResult(
                success=False,
                error=f"Literature operation {op!r} failed: {exc}",
                wall_time_seconds=time.monotonic() - t0,
            )

    # ------------------------------------------------------------------
    # Operations
    # ------------------------------------------------------------------

    async def _search(self, params: dict[str, Any]) -> ToolResult:
        """Search for papers across arXiv and/or Semantic Scholar."""
        query: str = params["query"]
        max_results: int = params.get("max_results", 10)
        sources: list[str] = params.get("sources", ["arxiv", "semantic_scholar"])

        all_papers: list[dict[str, Any]] = []
        errors: list[str] = []

        if "arxiv" in sources:
            try:
                arxiv_papers = await self._search_arxiv(query, max_results)
                all_papers.extend(arxiv_papers)
            except Exception as exc:
                logger.warning("arXiv search failed: %s", exc)
                errors.append(f"arXiv: {exc}")

        if "semantic_scholar" in sources:
            try:
                ss_papers = await self._search_semantic_scholar(query, max_results)
                all_papers.extend(ss_papers)
            except Exception as exc:
                logger.warning("Semantic Scholar search failed: %s", exc)
                errors.append(f"Semantic Scholar: {exc}")

        if not all_papers and errors:
            return ToolResult(
                success=False,
                error=f"All sources failed: {'; '.join(errors)}",
            )

        # Deduplicate by title (rough)
        seen_titles: set[str] = set()
        unique_papers: list[dict[str, Any]] = []
        for paper in all_papers:
            title_key = paper.get("title", "").lower().strip()
            if title_key not in seen_titles:
                seen_titles.add(title_key)
                unique_papers.append(paper)

        # Limit results
        unique_papers = unique_papers[:max_results]

        return ToolResult(
            success=True,
            output={
                "query": query,
                "n_results": len(unique_papers),
                "papers": unique_papers,
                "sources_queried": sources,
                "errors": errors,
            },
        )

    async def _fetch_paper(self, params: dict[str, Any]) -> ToolResult:
        """Fetch detailed metadata for a specific paper."""
        paper_id: str = params["paper_id"]
        source: str = params.get("source", "semantic_scholar")

        if source == "arxiv":
            paper = await self._fetch_arxiv_paper(paper_id)
        elif source == "semantic_scholar":
            paper = await self._fetch_semantic_scholar_paper(paper_id)
        else:
            return ToolResult(
                success=False,
                error=f"Unknown source: {source!r}; choose from ('arxiv', 'semantic_scholar')",
            )

        if paper is None:
            return ToolResult(
                success=False,
                error=f"Paper {paper_id!r} not found on {source}.",
            )

        return ToolResult(
            success=True,
            output={"paper": paper},
        )

    # ------------------------------------------------------------------
    # arXiv helpers
    # ------------------------------------------------------------------

    async def _search_arxiv(
        self, query: str, max_results: int
    ) -> list[dict[str, Any]]:
        """Query the arXiv API and parse the Atom feed."""
        params = urllib.parse.urlencode({
            "search_query": f"all:{query}",
            "start": 0,
            "max_results": max_results,
            "sortBy": "relevance",
            "sortOrder": "descending",
        })
        url = f"{_ARXIV_API}?{params}"

        response_text = await self._http_get(url)
        return self._parse_arxiv_feed(response_text)

    async def _fetch_arxiv_paper(
        self, arxiv_id: str
    ) -> dict[str, Any] | None:
        """Fetch a single arXiv paper by its ID."""
        url = f"{_ARXIV_API}?id_list={urllib.parse.quote(arxiv_id)}"
        response_text = await self._http_get(url)
        papers = self._parse_arxiv_feed(response_text)
        return papers[0] if papers else None

    @staticmethod
    def _parse_arxiv_feed(xml_text: str) -> list[dict[str, Any]]:
        """Parse an arXiv Atom feed into a list of paper dicts."""
        root = ElementTree.fromstring(xml_text)
        papers: list[dict[str, Any]] = []

        for entry in root.findall("atom:entry", _ARXIV_NS):
            title_el = entry.find("atom:title", _ARXIV_NS)
            summary_el = entry.find("atom:summary", _ARXIV_NS)
            published_el = entry.find("atom:published", _ARXIV_NS)
            id_el = entry.find("atom:id", _ARXIV_NS)

            authors = []
            for author_el in entry.findall("atom:author", _ARXIV_NS):
                name_el = author_el.find("atom:name", _ARXIV_NS)
                if name_el is not None and name_el.text:
                    authors.append(name_el.text.strip())

            # Extract DOI if present
            doi = None
            doi_el = entry.find("arxiv:doi", _ARXIV_NS)
            if doi_el is not None and doi_el.text:
                doi = doi_el.text.strip()

            title = title_el.text.strip().replace("\n", " ") if title_el is not None and title_el.text else ""
            abstract = summary_el.text.strip().replace("\n", " ") if summary_el is not None and summary_el.text else ""
            published = published_el.text.strip() if published_el is not None and published_el.text else ""
            arxiv_id = id_el.text.strip() if id_el is not None and id_el.text else ""

            year = int(published[:4]) if len(published) >= 4 else None

            papers.append({
                "title": title,
                "authors": authors,
                "abstract": abstract,
                "year": year,
                "doi": doi,
                "arxiv_id": arxiv_id.split("/abs/")[-1] if "/abs/" in arxiv_id else arxiv_id,
                "source": "arxiv",
            })

        return papers

    # ------------------------------------------------------------------
    # Semantic Scholar helpers
    # ------------------------------------------------------------------

    async def _search_semantic_scholar(
        self, query: str, max_results: int
    ) -> list[dict[str, Any]]:
        """Query the Semantic Scholar API."""
        params = urllib.parse.urlencode({
            "query": query,
            "limit": min(max_results, 100),
            "fields": "title,authors,abstract,year,externalIds,citationCount",
        })
        url = f"{_SEMANTIC_SCHOLAR_API}/paper/search?{params}"

        response_text = await self._http_get(url)
        data = json.loads(response_text)

        papers: list[dict[str, Any]] = []
        for item in data.get("data", []):
            authors = [
                a.get("name", "") for a in (item.get("authors") or [])
            ]
            external_ids = item.get("externalIds") or {}
            doi = external_ids.get("DOI")
            arxiv_id = external_ids.get("ArXiv")

            papers.append({
                "title": item.get("title", ""),
                "authors": authors,
                "abstract": item.get("abstract", ""),
                "year": item.get("year"),
                "doi": doi,
                "arxiv_id": arxiv_id,
                "citation_count": item.get("citationCount"),
                "semantic_scholar_id": item.get("paperId"),
                "source": "semantic_scholar",
            })

        return papers

    async def _fetch_semantic_scholar_paper(
        self, paper_id: str
    ) -> dict[str, Any] | None:
        """Fetch a single paper from Semantic Scholar by ID."""
        fields = "title,authors,abstract,year,externalIds,citationCount,references,venue"
        url = (
            f"{_SEMANTIC_SCHOLAR_API}/paper/{urllib.parse.quote(paper_id)}"
            f"?fields={fields}"
        )

        try:
            response_text = await self._http_get(url)
        except Exception:
            return None

        item = json.loads(response_text)
        if "error" in item or "title" not in item:
            return None

        authors = [a.get("name", "") for a in (item.get("authors") or [])]
        external_ids = item.get("externalIds") or {}
        references = [
            {
                "title": ref.get("title", ""),
                "year": ref.get("year"),
            }
            for ref in (item.get("references") or [])[:20]
        ]

        return {
            "title": item.get("title", ""),
            "authors": authors,
            "abstract": item.get("abstract", ""),
            "year": item.get("year"),
            "doi": external_ids.get("DOI"),
            "arxiv_id": external_ids.get("ArXiv"),
            "venue": item.get("venue", ""),
            "citation_count": item.get("citationCount"),
            "references": references,
            "source": "semantic_scholar",
        }

    # ------------------------------------------------------------------
    # HTTP helper
    # ------------------------------------------------------------------

    @staticmethod
    async def _http_get(url: str, timeout: float = 30.0) -> str:
        """Perform an HTTP GET request asynchronously.

        Uses ``urllib.request`` in a thread pool to avoid blocking the
        event loop while remaining dependency-free (no aiohttp/httpx
        required).
        """
        loop = asyncio.get_running_loop()

        def _fetch() -> str:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "AURORA/1.0"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", errors="replace")

        return await loop.run_in_executor(None, _fetch)
