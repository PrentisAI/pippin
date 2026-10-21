"""A project's literature, as agents and people use it.

:class:`ProjectLiterature` searches the indexes and records the search, checks what an agent
wrote for references, and applies the project's rule for the papers agents find:

* ``all``    every paper a search finds goes into the project's literature;
* ``cited``  the papers an agent cites go in; the rest wait in their search (the default);
* ``none``   nothing goes in by itself: people add papers from the searches.

Whatever the rule, a reference that is not verified never goes in.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from aurora.literature.check import Check, Reference, check, references_in
from aurora.literature.indexes import Indexes, indexes
from aurora.literature.library import IMPORT_RULES, library
from aurora.literature.records import Paper

logger = logging.getLogger(__name__)

Publish = Callable[..., Awaitable[None]]


@dataclass
class Citations:
    """What checking a text found."""

    verified: list[str] = field(default_factory=list)        # keys of real papers cited
    flagged: list[dict[str, Any]] = field(default_factory=list)   # {label, status, reason, closest_key}
    unknown_keys: list[str] = field(default_factory=list)    # [Key] that names no paper

    @property
    def clean(self) -> bool:
        return not self.flagged and not self.unknown_keys

    def note(self) -> str:
        """A short note for the transcript: what is real and what is not."""
        if not self.verified and self.clean:
            return ""
        lines = ["[Reference check by AURORA]"]
        if self.verified:
            lines.append("Verified: " + ", ".join(self.verified) + ".")
        for f in self.flagged:
            what = {"not_found": "not found in any index", "mismatch": "its identifier names another paper",
                    "unchecked": "could not be checked (indexes unreachable)"}[f["status"]]
            hint = f" The nearest real paper is [{f['closest_key']}]." if f.get("closest_key") else ""
            lines.append(f"“{f['label']}”: {what}. Treat what it supports as unsupported.{hint}")
        for k in self.unknown_keys:
            lines.append(f"[{k}] is not a paper from any search or the library: treat it as unsupported.")
        return "\n".join(lines)


def paper_card(entry: dict[str, Any], *, abstract_words: int = 60) -> str:
    """A paper as agents see it in a briefing: key, byline, title, a bit of the abstract."""
    p: Paper = entry["paper"]
    cy = p.cites_per_year()
    words = p.abstract.split()
    abstract = " ".join(words[:abstract_words]) + ("…" if len(words) > abstract_words else "")
    head = f"[{entry['key']}] {p.title} — {p.byline()}" + (f" · {cy:g} citations/yr" if cy is not None else "")
    out = head + (f"\n    {abstract}" if abstract else "")
    # a researcher's highlights and notes (from their Zotero) say what they found important; one-word
    # highlights (a marked term) say little out of context, so the fuller ones go first
    notes = [n for n in entry.get("notes") or [] if n.get("comment") or len(n["text"].split()) >= 4]
    notes.sort(key=lambda n: (not n.get("comment"), -len(n["text"])))
    for n in notes[:5]:
        where = f" (p. {n['page']})" if n.get("page") else ""
        text = re.sub(r"(\w)- (\w)", r"\1\2", n["text"])        # hyphenation the PDF's lines left behind
        what = f"“{text[:300]}”" if n["kind"] == "highlight" else text[:300]
        out += f"\n    Researcher's {n['kind']}{where}: {what}" + (f" — {n['comment'][:200]}" if n.get("comment") else "")
    return out


class ProjectLiterature:
    """The literature of one project (and, inside an experiment, of one run)."""

    def __init__(self, campaign_id: str, *, run_id: str = "", rule: str = "cited",
                 publish: Publish | None = None, idx: Indexes | None = None) -> None:
        self.campaign_id = campaign_id
        self.run_id = run_id
        self.rule = rule if rule in IMPORT_RULES else "cited"
        self._publish = publish
        self._idx = idx
        self.last_search_id = ""

    @property
    def idx(self) -> Indexes:
        return self._idx or indexes()

    async def _emit(self, topic: str, **data: Any) -> None:
        if self._publish is not None:
            try:
                await self._publish(topic, **data)
            except Exception:
                logger.debug("literature event %s failed", topic, exc_info=True)

    # ------------------------------------------------------------------ searching

    async def search(self, query: str, *, by: str, context: str = "", limit: int = 8) -> list[dict[str, Any]]:
        """Search every index; record the search; returns ``[{paper_id, key, paper}]``, best first."""
        found = await self.idx.search(query, limit)
        # records without authors are mostly index noise (abstract books, components of a DOI)
        papers = [p for p in found.papers if p.authors and p.title][: limit * 2]
        entries = [library.upsert_paper(p) for p in papers]
        sid = library.record_search(self.campaign_id, query=query, paper_ids=[e["paper_id"] for e in entries], by=by,
                                    context=context, run_id=self.run_id, reached=found.reached, failed=found.failed)
        if self.rule == "all":
            for e in entries:
                library.add_item(self.campaign_id, e["paper_id"], source="search", added_by=by, search_id=sid)
        await self._emit("literature_search", search_id=sid, by=by, query=query, context=context,
                         found=len(entries), reached=found.reached, failed=found.failed)
        notes = library.notes(self.campaign_id, [e["paper_id"] for e in entries])
        for e in entries:
            e["search_id"] = sid
            e["notes"] = notes.get(e["paper_id"], [])
        self.last_search_id = sid
        return entries

    # ------------------------------------------------------------------ citing

    def _cited(self, paper_id: str, *, by: str, place: str) -> None:
        library.record_use(self.campaign_id, place, paper_id=paper_id, run_id=self.run_id, by=by)
        library.mark_cited(self.campaign_id, paper_id, run_id=self.run_id)
        if self.rule in ("all", "cited"):
            library.add_item(self.campaign_id, paper_id, source="agent", added_by=by)

    async def check_reference(self, ref: Reference) -> Check:
        """Check one reference; a person's earlier decision about it counts."""
        known = library.known_ref(self.campaign_id, ref.to_dict())
        if known and known["resolution"] == "replaced" and known["paper_id"]:
            stored = library.paper(known["paper_id"])
            if stored:
                return Check("verified", ref, paper=stored["paper"], reason="replaced by a person")
        return await check(ref, self.idx)

    async def check_text(self, text: str, *, by: str, place: str) -> Citations:
        """Find the references in what an agent wrote, check them and record where they were used."""
        keys, refs = references_in(text)
        out = Citations()
        known = library.known_to(self.campaign_id) if keys else set()
        for key in keys:
            stored = library.paper(key)
            # a key counts only for a paper this project has seen: a made-up [Wang25] must not
            # turn into some other project's Wang25
            if stored is None or stored["paper_id"] not in known:
                out.unknown_keys.append(key)
                continue
            self._cited(stored["paper_id"], by=by, place=place)
            out.verified.append(stored["key"])
        for ref in refs:
            result = await self.check_reference(ref)
            if result.verified and result.paper is not None:
                stored = library.upsert_paper(result.paper)
                self._cited(stored["paper_id"], by=by, place=place)
                if stored["key"] not in out.verified:
                    out.verified.append(stored["key"])
                continue
            known = library.known_ref(self.campaign_id, ref.to_dict())
            if known and known["resolution"] in ("removed", "have"):
                if known["resolution"] == "have":        # a person vouched for it
                    library.record_use(self.campaign_id, place, ref_id=known["ref_id"], run_id=self.run_id, by=by)
                continue
            rid = library.record_ref(self.campaign_id, result, by=by, run_id=self.run_id)
            library.record_use(self.campaign_id, place, ref_id=rid, run_id=self.run_id, by=by)
            closest_key = ""
            if result.closest is not None:
                closest_key = library.upsert_paper(result.closest)["key"]
            flag = {"ref_id": rid, "label": ref.title or ref.label(), "status": result.status, "reason": result.reason,
                    "closest_key": closest_key}
            out.flagged.append(flag)
            await self._emit("reference_flagged", by=by, place=place, **flag)
        if out.verified or out.flagged or out.unknown_keys:
            await self._emit("references_checked", by=by, place=place, verified=out.verified,
                             flagged=len(out.flagged), unknown=out.unknown_keys)
        return out

    # ------------------------------------------------------------------ for people

    async def add(self, ref: Reference, *, by: str, source: str = "person", owner: str = "") -> Check:
        """A person adds a paper: it goes in only if it is verified."""
        result = await check(ref, self.idx)
        if result.verified and result.paper is not None:
            stored = library.upsert_paper(result.paper)
            library.add_item(self.campaign_id, stored["paper_id"], source=source, added_by=by, owner=owner,
                             cited_version=ref.arxiv_version)
        return result

    # ------------------------------------------------------------------ what a run cited

    def bibliography(self, run_id: str | None = None) -> list[dict[str, Any]]:
        """Real papers cited in a run (or the whole project), in the order first cited."""
        uses = library.uses(self.campaign_id, run_id)
        ids = [u["paper_id"] for u in uses if u["paper_id"]]
        for r in library.refs(self.campaign_id):
            if r["resolution"] == "replaced" and r["paper_id"] and any(u["ref_id"] == r["ref_id"] for u in uses):
                ids.append(r["paper_id"])
        stored = library.papers(ids)
        notes = library.notes(self.campaign_id, ids)
        return [{**stored[i], "notes": notes.get(i, [])} for i in dict.fromkeys(ids) if i in stored]

    def items(self) -> list[dict[str, Any]]:
        rows = library.items(self.campaign_id)
        stored = library.papers([r["paper_id"] for r in rows])
        return [{**r, **stored[r["paper_id"]]} for r in rows if r["paper_id"] in stored]

    def unresolved(self, run_id: str | None = None) -> list[dict[str, Any]]:
        """Flagged references nobody has decided about (of a run, or the whole project)."""
        return [r for r in library.refs(self.campaign_id)
                if not r["resolution"] and (run_id is None or r["run_id"] == run_id)]
