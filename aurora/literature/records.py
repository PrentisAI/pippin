"""A paper as the indexes describe it, and the text helpers used to compare two of them."""

from __future__ import annotations

import re
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from typing import Any

_DOI = re.compile(r"10\.\d{4,9}/[^\s\"<>]+", re.I)
_ARXIV = re.compile(r"(?:arxiv[:/\s]*|abs/|pdf/)?(\d{4}\.\d{4,5}|[a-z\-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?", re.I)
_TAGS = re.compile(r"<[^>]+>")


def clean_doi(text: str | None) -> str:
    """``10.1126/science.1219831`` from a DOI, a doi.org URL or a citation; '' if there is none."""
    m = _DOI.search(text or "")
    return m.group(0).rstrip(".,;)]").lower() if m else ""


def arxiv_version(text: str | None) -> int:
    """The version an arXiv ID or URL names (``1706.03762v2`` -> 2); 0 if none."""
    m = re.search(r"\d{4}\.\d{4,5}v(\d+)", text or "")
    return int(m.group(1)) if m else 0


def clean_arxiv(text: str | None) -> str:
    """``1706.03762`` (no version) from an arXiv ID or URL; '' if there is none."""
    text = (text or "").strip()
    if not text or (clean_doi(text) and "arxiv" not in text.lower()):
        return ""
    m = _ARXIV.search(text)
    return m.group(1) if m else ""


def clean_title(text: str) -> str:
    """A title as indexes give it, without markup: "Cu/ZnO/Al<sub>2</sub>O<sub>3</sub>" -> "Cu/ZnO/Al2O3"."""
    text = re.sub(r"\s*<(sub|sup)>\s*(.*?)\s*</\1>", r"\2", text or "", flags=re.I | re.S)
    text = re.sub(r"</?(i|b|em|strong|scp)>", "", text, flags=re.I)
    text = " ".join(_TAGS.sub(" ", text).split())
    return re.sub(r"(?<=[A-Za-z]\d) (?=[A-Z][a-z]?\d)", "", text)      # "Al2 O3" -> "Al2O3"


def clean_text(text: str) -> str:
    """Prose without markup (Crossref abstracts come as JATS XML): tags and a leading "Abstract" go."""
    text = re.sub(r"<jats:title>.*?</jats:title>", " ", text or "", flags=re.I | re.S)
    text = re.sub(r"<jats:related-article[^>]*>.*?</jats:related-article>", " ", text, flags=re.I | re.S)
    text = " ".join(_TAGS.sub(" ", text).split())
    text = re.sub(r"\s+([,.;:)])", r"\1", text).replace("( ", "(")
    return re.sub(r"^(abstract|summary)[:.]?\s+", "", text, flags=re.I)


def fold(text: str) -> str:
    """Lower case, no accents, markup or punctuation: for comparing titles and names."""
    text = _TAGS.sub("", text or "")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


def title_similarity(a: str, b: str) -> float:
    """0..1; word order and small differences (subtitles, punctuation, case) matter little."""
    fa, fb = fold(a), fold(b)
    if not fa or not fb:
        return 0.0
    if fa == fb:
        return 1.0
    whole = SequenceMatcher(None, fa, fb).ratio()
    ordered = SequenceMatcher(None, " ".join(sorted(fa.split())), " ".join(sorted(fb.split()))).ratio()
    # a subtitle one side leaves out: "X" vs "X: a study of Y" (not "X in Z", which is another paper)
    prefix = 0.0
    short, long_ = sorted((" ".join(_TAGS.sub("", a).split()), " ".join(_TAGS.sub("", b).split())), key=len)
    head = short.rstrip(" .?!").lower()
    if len(head) >= 20 and long_.lower().startswith(head) and long_[len(head):].lstrip()[:1] in (":", "-", "—", "–"):
        prefix = 0.95
    # a long added tail is not "small differences"
    if len(fb) > 1.3 * len(fa) or len(fa) > 1.3 * len(fb):
        whole, ordered = min(whole, 0.85), min(ordered, 0.85)
    return max(whole, ordered, prefix)


def family_name(author: str) -> str:
    """The family name of "Malte Behrens", "Behrens, Malte" or "M. Behrens", folded."""
    author = (author or "").strip()
    if "," in author:
        name = author.split(",")[0]
    else:
        parts = author.replace(".", ". ").split()
        name = parts[-1] if parts else ""
        if name.lower() in ("jr", "jr.", "ii", "iii") and len(parts) > 1:
            name = parts[-2]
    return fold(name).replace(" ", "")


def surname(author: str) -> str:
    """The family name as written ("Pablo-García" from "Sergio Pablo-García"), for showing."""
    author = (author or "").strip()
    if "," in author:
        return author.split(",")[0].strip()
    parts = author.split()
    if len(parts) > 1 and parts[-1].rstrip(".").lower() in ("jr", "ii", "iii"):
        return parts[-2]
    return parts[-1] if parts else ""


def same_person(a: str, b: str) -> bool:
    fa, fb = family_name(a), family_name(b)
    return bool(fa and fb) and (fa == fb or (min(len(fa), len(fb)) >= 4 and (fa in fb or fb in fa)))


@dataclass
class Paper:
    """What the indexes agree a paper is. Every Paper comes from an index, never from a model."""

    title: str
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    venue: str = ""
    doi: str = ""
    arxiv_id: str = ""
    openalex_id: str = ""
    s2_id: str = ""
    url: str = ""
    pdf_url: str = ""
    abstract: str = ""
    cited_by: int | None = None
    found_in: list[str] = field(default_factory=list)     # crossref | openalex | arxiv | semantic_scholar
    arxiv_version: int = 0          # the latest version on arXiv (0: unknown)
    arxiv_comment: str = ""         # the authors' comment on it (often what changed)

    @property
    def first_author(self) -> str:
        return self.authors[0] if self.authors else ""

    def short_key(self) -> str:
        """``Behrens12``: the first author's family name and the year, as the console shows papers."""
        name = family_name(self.first_author).capitalize() or fold(self.title).split(" ")[0].capitalize() or "Paper"
        return f"{name[:12]}{str(self.year)[-2:] if self.year else ''}"

    def cites_per_year(self) -> float | None:
        if self.cited_by is None or not self.year:
            return None
        years = max(1, time.gmtime().tm_year - self.year + 1)
        return round(self.cited_by / years, 1)

    def byline(self) -> str:
        names = [surname(a) for a in self.authors[:3]]
        who = ", ".join(n for n in names if n) + (" et al." if len(self.authors) > 3 else "")
        return " · ".join(x for x in (who, str(self.year or ""), self.venue) if x)

    def same_work(self, other: Paper) -> bool:
        """The same paper seen in two indexes (or a preprint and its published version)."""
        if self.doi and other.doi and self.doi == other.doi:
            return True
        if self.arxiv_id and other.arxiv_id and self.arxiv_id == other.arxiv_id:
            return True
        return (title_similarity(self.title, other.title) >= 0.93
                and (not self.authors or not other.authors or same_person(self.first_author, other.first_author)))

    def merge(self, other: Paper) -> None:
        """Fill what this record lacks from another record of the same work."""
        for name in ("doi", "arxiv_id", "openalex_id", "s2_id", "url", "pdf_url", "abstract", "venue"):
            if not getattr(self, name) and getattr(other, name):
                setattr(self, name, getattr(other, name))
        if not self.authors and other.authors:
            self.authors = list(other.authors)
        if self.year is None and other.year:
            self.year = other.year
        if other.arxiv_version > self.arxiv_version:
            self.arxiv_version, self.arxiv_comment = other.arxiv_version, other.arxiv_comment or self.arxiv_comment
        if other.cited_by is not None and (self.cited_by is None or other.cited_by > self.cited_by):
            self.cited_by = other.cited_by
        self.found_in = sorted(set(self.found_in) | set(other.found_in))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Paper:
        names = cls.__dataclass_fields__
        return cls(**{k: v for k, v in data.items() if k in names})


def merge_all(papers: list[Paper]) -> list[Paper]:
    """One record per work, in the order first seen."""
    out: list[Paper] = []
    for p in papers:
        twin = next((q for q in out if q.same_work(p)), None)
        if twin is None:
            out.append(p)
        else:
            twin.merge(p)
    return out
