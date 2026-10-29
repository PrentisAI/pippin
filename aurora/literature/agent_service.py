"""The literature for the research agents of one experiment: what ``aurora papers`` talks to.

The agents (Claude Code sessions, on the host or inside the container) search the scholarly
indexes, check references, read a paper's record and the researcher's highlights, download PDFs
into the workspace and record which papers their work uses.  Everything goes through the project's
literature (:class:`aurora.literature.service.ProjectLiterature`), so searches are recorded as the
agents', the project's rule for found papers applies, and nothing unverified enters the literature.

Requests arrive over a Unix socket (one JSON line in, one out), next to the cluster jobs' socket
and mounted into the container the same way.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any

import httpx

from aurora.literature import papers_client
from aurora.literature.check import Reference, references_in
from aurora.literature.library import library
from aurora.literature.records import clean_arxiv, clean_doi
from aurora.literature.service import ProjectLiterature, paper_card

logger = logging.getLogger(__name__)

CLIENT_PATH = Path(papers_client.__file__).resolve()
CONTAINER_CLIENT = "/aurora/lib/aurora_papers.py"
SOCKET_NAME = "papers.sock"
_MAX_PDF = 60 * 1024 * 1024


def parse_citation(text: str) -> Reference:
    """A reference from what an agent typed: a DOI, an arXiv ID or link, or a citation as written
    ("Behrens et al. 2012. The active site of methanol synthesis over Cu/ZnO/Al2O3. Science")."""
    text = " ".join(text.split())
    if clean_doi(text):
        return Reference(doi=clean_doi(text), text=text)
    if clean_arxiv(text) and (re.fullmatch(r"\s*(arxiv:)?\s*\d{4}\.\d{4,5}(v\d+)?\s*", text, re.I) or "arxiv" in text.lower()):
        return Reference(arxiv_id=clean_arxiv(text), text=text)
    year_m = re.search(r"\b(19|20)\d{2}\b", text)
    year = int(year_m.group(0)) if year_m else None
    authors, rest = "", text
    if m := re.match(r"^(.{2,200}?)\s*\((?:19|20)\d{2}[a-z]?\)[.,:]?\s+(.+)$", text):   # "Kuld S, Thorhauge M (2016) Title"
        authors, rest = m.group(1), m.group(2)
    elif m := re.match(r"^(.{2,200}?\bet al\.?)[\s,]+(.+)$", text):          # "Behrens, Studt et al. Title ..."
        authors, rest = m.group(1), m.group(2)
    parts = [p.strip(" .,;()") for p in re.split(r"\.\s+|\s+[-–—]\s+", rest) if p.strip(" .,;()")]
    if not authors and len(parts) > 1 and (re.search(r",|&| and ", parts[0]) or re.search(r"\b(19|20)\d{2}\b", parts[0])) \
            and len(parts[0].split()) <= 12:
        authors = parts.pop(0)
    authors = re.sub(r"\(?\b(19|20)\d{2}\b\)?", "", authors).strip(" ,.")
    # the title: the longest part that is not only a venue and a year
    parts = [p for p in parts if not re.fullmatch(r"[A-Z][\w&. ]{0,40}\s*\(?\b(19|20)\d{2}\b\)?", p)] or parts
    title = max(parts, key=len) if parts else text
    title = re.sub(r"\(?\b(19|20)\d{2}\b\)?\.?$", "", title).strip()
    return Reference(title=title, authors=authors, year=year, text=text)


class PaperService:
    """Serves ``aurora papers`` for one experiment."""

    allowed_tools = ["Bash(aurora papers *)"]

    def __init__(self, literature: ProjectLiterature, *, socket_dir: Path, workspace_dir: Path,
                 project_files: Path | None = None) -> None:
        self.literature = literature
        self.socket_path = Path(socket_dir) / SOCKET_NAME
        self.workspace_dir = Path(workspace_dir)
        self.project_files = project_files
        self._server: asyncio.base_events.Server | None = None

    # ------------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(FileNotFoundError):
            self.socket_path.unlink()
        self._server = await asyncio.start_unix_server(self._handle, path=str(self.socket_path))
        os.chmod(self.socket_path, 0o600)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            with contextlib.suppress(Exception):
                await self._server.wait_closed()
        with contextlib.suppress(FileNotFoundError):
            self.socket_path.unlink()

    def agent_env(self, in_container: bool = False) -> dict[str, str]:
        if in_container:
            from aurora.compute.slurm import CONTAINER_SOCKET_DIR
            return {"AURORA_PAPERS_SOCKET": f"{CONTAINER_SOCKET_DIR}/{SOCKET_NAME}", "AURORA_PAPERS_APP": "/app"}
        bin_dir = str(Path(sys.executable).parent)
        return {"AURORA_PAPERS_SOCKET": str(self.socket_path), "AURORA_PAPERS_APP": str(self.workspace_dir),
                "PATH": bin_dir + os.pathsep + os.environ.get("PATH", "/usr/bin:/bin")}

    def agent_prompt(self, in_container: bool = False) -> str:
        return (
            "\n## The literature (`aurora papers`)\n"
            "When you need prior work, a method's source or a number from a paper, look it up instead of "
            "citing from memory:\n"
            "  aurora papers search \"<what you look for>\" [--limit 8]   OpenAlex, arXiv, Crossref, Semantic Scholar\n"
            "  aurora papers show <Key|DOI|arXiv ID>    the abstract, links, and the researcher's highlights and notes\n"
            "  aurora papers pdf <Key> [--text]         download its PDF into the workspace's papers/ (and its text)\n"
            "  aurora papers check \"<reference>\" ...    is it a real paper? (or --file refs.bib)\n"
            "  aurora papers list                       the project's papers and their keys\n"
            "  aurora papers cite <Key> ...             record that your work uses them\n"
            "  aurora papers bib [<Key> ...]            BibTeX for them\n"
            "Cite papers as [Key] (for example [Behrens12]) in what you write; every reference in the report "
            "is checked, and one that no index has is reported as unsupported. Never invent a reference. "
            + ("Run `aurora papers` as a plain command.\n" if in_container
               else "Run `aurora papers` directly, not inside docker exec.\n")
        )

    # ------------------------------------------------------------------ requests

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            line = await reader.readline()
            req = json.loads(line or b"{}")
            handler = {"search": self.search, "check": self.check, "show": self.show, "list": self.list,
                       "bib": self.bib, "cite": self.cite, "pdf": self.pdf}.get(req.get("op", ""))
            if handler is None:
                answer: dict[str, Any] = {"ok": False, "error": f"unknown action {req.get('op')!r}"}
            else:
                answer = {"ok": True, "text": await handler(req)}
        except ValueError as exc:
            answer = {"ok": False, "error": str(exc)}
        except Exception as exc:
            logger.warning("aurora papers failed", exc_info=True)
            answer = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        try:
            writer.write((json.dumps(answer) + "\n").encode())
            await writer.drain()
        finally:
            writer.close()

    @staticmethod
    def _who(req: dict[str, Any]) -> str:
        job = req.get("agent_job") or ""
        return f"Research agent ({job})" if job else "Research agent"

    def _paper(self, ident: str) -> dict[str, Any]:
        ident = ident.strip().strip("[]")
        entry = library.paper(ident)
        if entry is None:
            doi, aid = clean_doi(ident), clean_arxiv(ident)
            for e in self.literature.items():
                p = e["paper"]
                if (doi and p.doi == doi) or (aid and p.arxiv_id == aid):
                    return e
            raise ValueError(f"no paper {ident!r} in this project's literature or searches; "
                             "`aurora papers search` or `aurora papers check` it first")
        return entry

    async def search(self, req: dict[str, Any]) -> str:
        query = str(req.get("query", "")).strip()
        if not query:
            raise ValueError("search for what?")
        entries = await self.literature.search(query, by=self._who(req), context="Research agent",
                                               limit=int(req.get("limit") or 8))
        s = library.search(self.literature.last_search_id) or {}
        note = f"\n({', '.join(s['failed'])} did not answer.)" if s.get("failed") else ""
        if not entries:
            return f"Nothing found for “{query}”.{note}"
        lines = [f"{len(entries)} papers for “{query}” (cite them as [Key]; `aurora papers show <Key>` for more):", ""]
        lines += [paper_card(e, abstract_words=45) for e in entries]
        return "\n".join(lines) + note

    async def check(self, req: dict[str, Any]) -> str:
        refs = [parse_citation(r) for r in req.get("refs") or [] if str(r).strip()]
        text = str(req.get("text") or "")
        if text:
            from aurora.literature.bibtex import parse_any
            parsed = [r for _, r in parse_any(text)]
            if not parsed:
                keys, parsed = references_in(text)
                for k in keys:
                    entry = library.paper(k)
                    refs.append(Reference(title=entry["paper"].title, doi=entry["paper"].doi,
                                          arxiv_id=entry["paper"].arxiv_id, text=f"[{k}]") if entry
                                else Reference(title=k, text=f"[{k}]"))
            refs += parsed
        if not refs:
            raise ValueError("no reference found to check")
        out = []
        for ref in refs[:40]:
            result = await self.literature.check_reference(ref)
            label = ref.text or ref.title or ref.label()
            if result.verified and result.paper is not None:
                stored = library.upsert_paper(result.paper)
                out.append(f"✓ real: [{stored['key']}] {result.paper.title} — {result.paper.byline()}"
                           f"  (asked: {label[:120]})")
            else:
                what = {"not_found": "NOT FOUND in any index: do not cite it",
                        "mismatch": "its identifier names ANOTHER paper",
                        "unchecked": "could not be checked now (indexes unreachable): try again later"}[result.status]
                line = f"✗ {label[:160]}: {what}."
                if result.reason:
                    line += f" {result.reason}."
                if result.closest is not None:
                    c = library.upsert_paper(result.closest)
                    line += f"\n    The nearest real paper: [{c['key']}] {result.closest.title} — {result.closest.byline()}"
                out.append(line)
        return "\n".join(out)

    async def show(self, req: dict[str, Any]) -> str:
        e = self._paper(str(req.get("paper", "")))
        p = e["paper"]
        notes = library.notes(self.literature.campaign_id, [e["paper_id"]]).get(e["paper_id"], [])
        card = paper_card({**e, "notes": notes}, abstract_words=400)
        links = [x for x in (f"https://doi.org/{p.doi}" if p.doi else "", f"https://arxiv.org/abs/{p.arxiv_id}" if p.arxiv_id else "",
                             p.pdf_url, p.url if not (p.doi or p.arxiv_id) else "") if x]
        extra = [f"Venue: {p.venue}" if p.venue else "", f"Cited by {p.cited_by}" if p.cited_by is not None else "",
                 f"Found in: {', '.join(p.found_in)}" if p.found_in else "", "Links: " + " · ".join(links) if links else "",
                 f"arXiv version: v{p.arxiv_version}" + (f" ({p.arxiv_comment})" if p.arxiv_comment else "") if p.arxiv_version else ""]
        in_project = library.has_item(self.literature.campaign_id, e["paper_id"])
        extra.append("In the project's literature." if in_project else "Not in the project's literature yet (`aurora papers cite` adds it).")
        return card + "\n" + "\n".join(x for x in extra if x)

    async def list(self, req: dict[str, Any]) -> str:
        items = self.literature.items()
        if not items:
            return "The project's literature is empty. `aurora papers search` to find papers."
        return f"{len(items)} papers in the project's literature:\n" + "\n".join(
            f"[{e['key']}] {e['paper'].title} — {e['paper'].byline()}" for e in items)

    async def bib(self, req: dict[str, Any]) -> str:
        from aurora.literature.bibtex import to_bibtex
        keys = req.get("keys") or []
        entries = [self._paper(k) for k in keys] if keys else self.literature.items()
        return to_bibtex(entries) or "% no papers"

    async def cite(self, req: dict[str, Any]) -> str:
        out = []
        for k in req.get("keys") or []:
            e = self._paper(k)
            self.literature._cited(e["paper_id"], by=self._who(req), place="Research")
            out.append(f"[{e['key']}] recorded as used by this run.")
        return "\n".join(out)

    async def pdf(self, req: dict[str, Any]) -> str:
        e = self._paper(str(req.get("paper", "")))
        p = e["paper"]
        target = self.workspace_dir / "papers" / f"{e['key']}.pdf"
        target.parent.mkdir(parents=True, exist_ok=True)
        source = ""
        # the researcher's own copy first (from their Zotero, in the project's files)
        if self.project_files is not None:
            for name in (e["key"],):
                f = self.project_files / "papers" / f"{name}.pdf"
                if f.is_file():
                    target.write_bytes(f.read_bytes())
                    source = "the project's files"
                    break
        if not source:
            url = p.pdf_url or (f"https://arxiv.org/pdf/{p.arxiv_id}" if p.arxiv_id else "")
            if not url:
                raise ValueError(f"no open-access PDF is known for [{e['key']}]; its record: "
                                 + (f"https://doi.org/{p.doi}" if p.doi else p.url or "none"))
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0), follow_redirects=True) as c:
                    r = await c.get(url, headers={"User-Agent": "Pippin research assistant"})
            except httpx.HTTPError as exc:
                raise ValueError(f"the PDF could not be downloaded ({type(exc).__name__})") from exc
            if r.status_code >= 400 or not r.content.startswith(b"%PDF") or len(r.content) > _MAX_PDF:
                raise ValueError(f"no PDF at {url} (it may be behind a paywall)")
            target.write_bytes(r.content)
            source = url
        app = str(req.get("app") or "").rstrip("/")
        where = f"{app}/papers" if app else "papers"
        lines = [f"Saved {where}/{target.name} ({target.stat().st_size // 1024} KB, from {source})."]
        if req.get("text"):
            try:
                from pypdf import PdfReader
                text = "\n\n".join((page.extract_text() or "") for page in PdfReader(str(target)).pages)
                (target.with_suffix(".txt")).write_text(text)
                lines.append(f"Its text: {where}/{target.with_suffix('.txt').name} ({len(text.split())} words).")
            except Exception as exc:
                lines.append(f"Its text could not be extracted ({type(exc).__name__}).")
        return "\n".join(lines)
