"""Checking a reference: is the paper an agent (or a person) cites real, and which one is it?

A reference is looked up by DOI or arXiv ID first, else by title (and authors and year),
in every index.  A match must agree on the title and, where the reference gives them, on the
first author and the year (a year either way, for a preprint and its journal version).

The answer is one of:

* ``verified``   the paper exists; :attr:`Check.paper` is what the indexes say it is;
* ``mismatch``   the DOI or arXiv ID exists but names a different paper than the title cited;
* ``not_found``  every index answered, and none has it (most likely made up);
* ``unchecked``  no index could be asked (offline): never treated as verified.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from aurora.literature.indexes import INDEX_NAMES, Found, Indexes, indexes
from aurora.literature.records import Paper, clean_arxiv, clean_doi, fold, same_person, title_similarity

TITLE_MATCH = 0.90          # a cited title and an indexed one agree
ID_TITLE_MATCH = 0.70       # a cited title and the paper its DOI/arXiv ID names agree (looser: wording varies)
CLOSEST_MIN = 0.55          # below this a "closest real paper" is not worth showing


@dataclass
class Reference:
    """A paper as cited: whatever the citer gave."""

    title: str = ""
    authors: str = ""           # "Behrens, Studt" or "Behrens et al." (the first one matters)
    year: int | None = None
    doi: str = ""
    arxiv_id: str = ""
    text: str = ""              # the citation as written, for showing it back
    truncated: bool = False     # the title may be cut off (a file name): a longer indexed title can still match

    def __post_init__(self) -> None:
        self.doi = clean_doi(self.doi) or clean_doi(self.text)
        self.arxiv_id = clean_arxiv(self.arxiv_id) or (clean_arxiv(self.text) if "arxiv" in self.text.lower() else "")
        self.title = " ".join((self.title or "").split()).strip(" .\"'“”")

    @property
    def first_author(self) -> str:
        first = re.split(r",|;| and | & | et al", self.authors or "", maxsplit=1)[0].strip()
        return first

    def label(self) -> str:
        who = self.first_author.split()[-1] if self.first_author else ""
        bits = [b for b in (who, str(self.year or "")) if b]
        return " ".join(bits) or self.title[:60] or self.doi or self.arxiv_id or self.text[:60]

    def to_dict(self) -> dict[str, Any]:
        return {"title": self.title, "authors": self.authors, "year": self.year, "doi": self.doi,
                "arxiv_id": self.arxiv_id, "text": self.text}


@dataclass
class Check:
    status: str                              # verified | mismatch | not_found | unchecked
    reference: Reference
    paper: Paper | None = None
    closest: Paper | None = None             # for not_found / mismatch: the nearest real paper
    searched: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    reason: str = ""

    @property
    def verified(self) -> bool:
        return self.status == "verified"

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "reference": self.reference.to_dict(),
                "paper": self.paper.to_dict() if self.paper else None,
                "closest": self.closest.to_dict() if self.closest else None,
                "searched": self.searched, "failed": self.failed, "reason": self.reason}


def _names(ids: list[str]) -> str:
    names = [INDEX_NAMES.get(i, i) for i in ids]
    return ", ".join(names[:-1]) + (" or " if len(names) > 1 else "") + names[-1] if names else "no index"


def agrees(ref: Reference, paper: Paper, title_min: float = TITLE_MATCH) -> tuple[bool, str]:
    """Whether an indexed paper is the one cited; if not, why."""
    if ref.title and title_similarity(ref.title, paper.title) < title_min:
        head, full = fold(ref.title), fold(paper.title)
        if not (ref.truncated and len(head) >= 25 and full.startswith(head)):
            return False, "the title differs"
    if ref.first_author and paper.authors and not any(same_person(ref.first_author, a) for a in paper.authors[:2]):
        return False, "the first author differs"
    if ref.year and paper.year and abs(ref.year - paper.year) > 1:
        return False, f"the year differs ({paper.year})"
    return True, ""


def _enriched(paper: Paper, found: Found) -> Paper:
    """The matching record, completed from the other indexes' records of the same work."""
    out = Paper.from_dict(paper.to_dict())
    for other in found.raw:
        # a record that disagrees on the year is a different version or a bad record: its DOI would mislead
        if other is not paper and out.same_work(other) and not (out.year and other.year and abs(out.year - other.year) > 1):
            out.merge(other)
    return out


def _best(ref: Reference, papers: list[Paper]) -> Paper | None:
    if not papers:
        return None
    if not ref.title:
        return papers[0]
    return max(papers, key=lambda p: title_similarity(ref.title, p.title))


async def check(ref: Reference, idx: Indexes | None = None) -> Check:
    """Look a reference up in the indexes and say whether it is real."""
    idx = idx or indexes()
    if not (ref.title or ref.doi or ref.arxiv_id):
        return Check("not_found", ref, reason="the reference names no title, DOI or arXiv ID")
    reached: set[str] = set()
    failed: set[str] = set()

    def note(found: Found) -> None:
        reached.update(found.reached)
        failed.update(found.failed)

    # by identifier: the ID must exist, and if a title was cited, it must be roughly that paper
    for kind, ident in (("doi", ref.doi), ("arxiv", ref.arxiv_id)):
        if not ident:
            continue
        found = await (idx.by_doi(ident) if kind == "doi" else idx.by_arxiv(ident))
        note(found)
        if found.papers:
            paper = found.papers[0]
            ok, why = agrees(ref, paper, ID_TITLE_MATCH)
            for record in found.raw:         # any index's own record may be the one that agrees
                if not ok and agrees(ref, record, ID_TITLE_MATCH)[0]:
                    ok, paper = True, _enriched(record, found)
            if ok:
                return Check("verified", ref, paper=paper, searched=sorted(reached), failed=sorted(failed - reached))
            if not ref.title:
                break
            # the ID is real but points elsewhere: maybe only the ID is wrong; try the title
            looked = await idx.look_up(ref.title, ref.authors, ref.year)
            note(looked)
            for cand in looked.raw:
                if agrees(ref, cand)[0]:
                    return Check("verified", ref, paper=_enriched(cand, looked), searched=sorted(reached), failed=sorted(failed - reached),
                                 reason=f"the {kind.upper() if kind == 'doi' else 'arXiv ID'} cited belongs to another "
                                        f"paper; the title, authors and year match this one")
            label = "DOI" if kind == "doi" else "arXiv ID"
            return Check("mismatch", ref, closest=paper, searched=sorted(reached), failed=sorted(failed - reached),
                         reason=f"the {label} {ident} is “{paper.title}” ({paper.byline()}): {why}")

    if ref.title:
        looked = await idx.look_up(ref.title, ref.authors, ref.year)
        note(looked)
        for cand in sorted(looked.raw, key=lambda p: -title_similarity(ref.title, p.title)):
            if agrees(ref, cand)[0]:
                return Check("verified", ref, paper=_enriched(cand, looked), searched=sorted(reached),
                             failed=sorted(failed - reached))
        closest = _best(ref, looked.papers)
        if closest is not None and title_similarity(ref.title, closest.title) < CLOSEST_MIN:
            closest = None
    else:
        closest = None

    if not reached:
        return Check("unchecked", ref, searched=[], failed=sorted(failed),
                     reason="no index could be reached; the reference is not checked yet")
    what = "this title" + (" and these authors" if ref.authors else "")
    if ref.doi or ref.arxiv_id:
        what = "this " + ("DOI" if ref.doi else "arXiv ID") + (" or title" if ref.title else "")
    return Check("not_found", ref, closest=closest, searched=sorted(reached), failed=sorted(failed - reached),
                 reason=f"no paper with {what} in {_names(sorted(reached))}"
                        + (f" ({_names(sorted(failed - reached))} could not be reached)" if failed - reached else ""))


# ---------------------------------------------------------------------------
# Finding references in what an agent wrote

# [ref: Title; Authors; Year; DOI or arXiv]  (the form agents are asked to use for papers they bring)
_REF_TAG = re.compile(r"\[ref:\s*([^\]]+)\]", re.I)
_ONE_KEY = r"[A-Z][A-Za-z\-']{1,16}\d{2}[a-z]?"
# [Behrens12], and groups: [Wang25, Gumaan25] or [Kuld16; Behrens12]
_KEY = re.compile(rf"\[({_ONE_KEY}(?:\s*[,;]\s*{_ONE_KEY})*)\]")
_URL = re.compile(r"https?://[^\s)\]>\"']+")


def parse_ref_tag(body: str) -> Reference:
    parts = [p.strip() for p in body.split(";")]
    ref = Reference(text=f"[ref: {body.strip()}]")
    rest = []
    for p in parts:
        if not p:
            continue
        if clean_doi(p):
            ref.doi = clean_doi(p)
        elif "arxiv" in p.lower() or re.fullmatch(r"\d{4}\.\d{4,5}(v\d+)?", p):
            ref.arxiv_id = clean_arxiv(p)
        elif re.fullmatch(r"(19|20)\d{2}", p):
            ref.year = int(p)
        else:
            rest.append(p)
    if rest:
        ref.title = rest[0].strip(" .\"'“”")
    if len(rest) > 1:
        ref.authors = rest[1]
    return ref


def references_in(text: str) -> tuple[list[str], list[Reference]]:
    """The library keys cited as ``[Key]`` and the other references (tags, DOIs, arXiv links) in a text."""
    keys = list(dict.fromkeys(k for group in _KEY.findall(text or "") for k in re.split(r"\s*[,;]\s*", group)))
    refs: list[Reference] = []
    seen: set[str] = set()
    for m in _REF_TAG.finditer(text or ""):
        r = parse_ref_tag(m.group(1))
        sig = r.doi or r.arxiv_id or fold(r.title)
        if sig and sig not in seen:
            seen.add(sig)
            refs.append(r)
    stripped = _REF_TAG.sub(" ", text or "")
    for url in _URL.findall(stripped):
        doi, arx = clean_doi(url), (clean_arxiv(url) if "arxiv.org" in url else "")
        sig = doi or arx
        if sig and sig not in seen:
            seen.add(sig)
            refs.append(Reference(doi=doi, arxiv_id=arx, text=url))
    for m in re.finditer(r"\b(?:doi:\s*)?(10\.\d{4,9}/[^\s\"<>\])]+)", stripped, re.I):
        doi = clean_doi(m.group(1))
        if doi and doi not in seen:
            seen.add(doi)
            refs.append(Reference(doi=doi, text=m.group(0)))
    for m in re.finditer(r"\barXiv:\s*(\d{4}\.\d{4,5})", stripped, re.I):
        if m.group(1) not in seen:
            seen.add(m.group(1))
            refs.append(Reference(arxiv_id=m.group(1), text=m.group(0)))
    return keys, refs
