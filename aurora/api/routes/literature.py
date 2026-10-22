"""The literature: a project's papers, searches and flagged references, and each person's Library.

* ``/api/campaigns/{id}/literature``: the project's papers (with where they were used), the searches
  agents and people ran, and the references that could not be verified, with what to do about them;
* ``/api/library``: your own library, everything in the projects you can see, and searching the indexes.

Papers enter only once they are verified; a flagged reference stays a flag until someone decides.
"""

from __future__ import annotations

import re
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from aurora.auth.guard import current_user, require, visible
from aurora.literature.bibtex import parse_any, to_bibtex
from aurora.literature.check import Reference
from aurora.literature.indexes import INDEX_NAMES
from aurora.literature.library import IMPORT_RULES, library
from aurora.literature.records import Paper, clean_arxiv, clean_doi, clean_text
from aurora.literature.service import ProjectLiterature

project_router = APIRouter(prefix="/api/campaigns", tags=["literature"])
library_router = APIRouter(prefix="/api/library", tags=["literature"])

L = "/{campaign_id}/literature"
SOURCE_LABELS = {"agent": "Cited by an agent", "search": "Found in a search", "person": "Added by hand",
                 "bibtex": "Imported from BibTeX", "zotero": "From Zotero", "feed": "From the feed"}


def _who() -> str:
    user = current_user()
    return "You" if user.is_local else user.name


def _owner() -> str:
    return current_user().user_id


def _rule(campaign_id: str = "") -> str:
    """Papers the agents find: the project's own setting, else the console's default."""
    own = library.project_rule(campaign_id) if campaign_id else ""
    if own:
        return own
    from aurora.api.routes.campaigns import base_config
    try:
        return (base_config().get("literature") or {}).get("add_found") or "cited"
    except Exception:
        return "cited"


def _project(campaign_id: str) -> None:
    from aurora.api.routes.campaigns import _ensure_campaign
    _ensure_campaign(campaign_id)       # 404 for a project that does not exist


def paper_view(entry: dict[str, Any]) -> dict[str, Any]:
    p: Paper = entry["paper"]
    return {"paper_id": entry["paper_id"], "key": entry["key"], "title": p.title, "authors": p.authors,
            "year": p.year, "venue": p.venue, "doi": p.doi, "arxiv_id": p.arxiv_id, "url": p.url,
            "pdf_url": p.pdf_url, "abstract": clean_text(p.abstract), "cited_by": p.cited_by,
            "cites_per_year": p.cites_per_year(), "found_in": [INDEX_NAMES.get(i, i) for i in p.found_in],
            "byline": p.byline()}


def _uses_by(campaign_id: str) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    by_paper: dict[str, list[str]] = {}
    by_ref: dict[str, list[str]] = {}
    for u in library.uses(campaign_id):
        target = by_paper.setdefault(u["paper_id"], []) if u["paper_id"] else by_ref.setdefault(u["ref_id"], [])
        if u["place"] not in target:
            target.append(u["place"])
    return by_paper, by_ref


def _search_view(s: dict[str, Any], in_library: set[str]) -> dict[str, Any]:
    stored = library.papers([r["paper_id"] for r in s["results"]])
    rows = [{**paper_view(stored[r["paper_id"]]), "cited": bool(r["cited"]), "in_library": r["paper_id"] in in_library}
            for r in s["results"] if r["paper_id"] in stored]
    return {"search_id": s["search_id"], "run_id": s["run_id"], "by": s["by"], "context": s["context"],
            "query": s["query"], "created_at": s["created_at"],
            "reached": [INDEX_NAMES.get(i, i) for i in s["reached"]],
            "failed": [INDEX_NAMES.get(i, i) for i in s["failed"]],
            "found": len(rows), "cited": sum(r["cited"] for r in rows), "in_library": sum(r["in_library"] for r in rows),
            "papers": rows}


def _zotero(campaign_id: str) -> tuple[set[str], dict[str, Any] | None]:
    """The papers in the asking person's Zotero, and their collection for this project (if any)."""
    from aurora.literature.zotero import zotero_store
    me = current_user().user_id
    link = zotero_store.link(me)
    if link is None:
        return set(), None
    maps = [m for m in zotero_store.maps(me) if m["campaign_id"] == campaign_id]
    return zotero_store.papers_in(me), {
        "connected": True, "collections": [m["name"] for m in maps], "can_send": link["method"] == "web" and bool(maps),
        "last_sync": link["last_sync"], "last_error": link["last_error"]}


def _literature(campaign_id: str) -> dict[str, Any]:
    uses, ref_uses = _uses_by(campaign_id)
    items = library.items(campaign_id)
    stored = library.papers([i["paper_id"] for i in items])
    in_library = {i["paper_id"] for i in items}
    in_zotero, zotero = _zotero(campaign_id)
    notes = library.notes(campaign_id)
    pdfs = _pdfs(campaign_id)
    papers = [{**paper_view(stored[i["paper_id"]]), "source": i["source"],
               "source_label": SOURCE_LABELS.get(i["source"], i["source"]), "added_by": i["added_by"],
               "search_id": i["search_id"], "starred": bool(i["starred"]), "added_at": i["added_at"],
               "used_in": uses.get(i["paper_id"], []), "in_zotero": i["paper_id"] in in_zotero,
               "notes": [{"kind": n["kind"], "text": n["text"], "comment": n["comment"], "page": n["page"],
                          "by": _person(n["owner"])} for n in notes.get(i["paper_id"], [])],
               "pdf_file": pdfs.get(stored[i["paper_id"]]["key"], ""),
               "cited_version": i.get("cited_version") or 0,
               "newer_version": bool(i.get("cited_version") and stored[i["paper_id"]]["paper"].arxiv_version > i["cited_version"]),
               "arxiv_version": stored[i["paper_id"]]["paper"].arxiv_version,
               "arxiv_comment": stored[i["paper_id"]]["paper"].arxiv_comment}
              for i in items if i["paper_id"] in stored]
    refs = []
    for r in library.refs(campaign_id):
        closest = Paper.from_dict(r["closest"]) if r["closest"] else None
        closest_entry = library.upsert_paper(closest) if closest else None
        refs.append({"ref_id": r["ref_id"], "status": r["status"], "reference": r["reference"], "reason": r["reason"],
                     "searched": [INDEX_NAMES.get(i, i) for i in r["searched"]], "by": r["by"], "run_id": r["run_id"],
                     "resolution": r["resolution"], "decided_by": r["decided_by"], "created_at": r["created_at"],
                     "closest": paper_view(closest_entry) if closest_entry else None,
                     "replaced_by": (library.paper(r["paper_id"]) or {}).get("key", "") if r["paper_id"] else "",
                     "used_in": ref_uses.get(r["ref_id"], [])})
    searches = [_search_view(s, in_library) for s in library.searches(campaign_id)]
    for s in searches:      # the list shows the searches; their papers come with GET .../searches/{id}
        s.pop("papers")
    open_refs = [r for r in refs if not r["resolution"]]
    return {"rule": _rule(campaign_id), "rule_own": bool(library.project_rule(campaign_id)), "default_rule": _rule(),
            "papers": papers, "refs": refs, "searches": searches, "zotero": zotero,
            "counts": {"papers": len(papers), "flagged": len(open_refs),
                       "checked": sum(1 for p in papers if p["found_in"]), "searches": len(searches)}}


def _person(user_id: str) -> str:
    if user_id in ("", "local"):
        return "You"
    from aurora.auth.store import auth_store
    row = auth_store.get_user(user_id)
    return row["name"] if row else "someone"


def _pdfs(campaign_id: str) -> dict[str, str]:
    """PDFs in the project's files under papers/, by the file's stem (a key or citation key)."""
    from aurora.api.routes.files import project_files_dir
    from aurora.literature.zotero import zotero_store
    folder = project_files_dir(campaign_id) / "papers"
    if not folder.is_dir():
        return {}
    files = {f.stem: f"papers/{f.name}" for f in folder.glob("*.pdf")}
    # a PDF named by its Better BibTeX key belongs to the paper with that key
    by_key: dict[str, str] = {}
    keys = zotero_store.citekeys([i["paper_id"] for i in library.items(campaign_id)])
    for pid, ck in keys.items():
        entry = library.paper(pid)
        if entry and ck in files:
            by_key[entry["key"]] = files[ck]
    return {**files, **by_key}


# ---------------------------------------------------------------------------
# A project's literature


@project_router.get(L)
async def get_literature(campaign_id: str) -> dict[str, Any]:
    _project(campaign_id)
    return _literature(campaign_id)


@project_router.get(L + "/searches/{search_id}")
async def get_search(campaign_id: str, search_id: str) -> dict[str, Any]:
    s = library.search(search_id)
    if s is None or s["campaign_id"] != campaign_id:
        raise HTTPException(404, "No such search")
    return _search_view(s, {i["paper_id"] for i in library.items(campaign_id)})


class SearchBody(BaseModel):
    query: str
    limit: int = 10


@project_router.post(L + "/search")
async def search_project(campaign_id: str, body: SearchBody) -> dict[str, Any]:
    """A person searches the indexes for a project (the search is kept with the project's searches)."""
    _project(campaign_id)
    if not body.query.strip():
        raise HTTPException(400, "Type something to search for")
    lit = ProjectLiterature(campaign_id, rule="none")       # a person's search adds nothing by itself
    await lit.search(body.query.strip(), by=_who(), context="Searched in the console", limit=min(body.limit, 25))
    return _search_view(library.search(lit.last_search_id), {i["paper_id"] for i in library.items(campaign_id)})


class AddBody(BaseModel):
    # a paper by any of these; it is checked before it goes in
    title: str = ""
    authors: str = ""
    year: int | None = None
    doi: str = ""
    arxiv_id: str = ""
    text: str = ""          # a DOI, arXiv ID or URL pasted as is


@project_router.post(L + "/papers")
async def add_paper(campaign_id: str, body: AddBody) -> dict[str, Any]:
    """Add a paper by DOI, arXiv ID or title: it goes in only if it is found in an index."""
    _project(campaign_id)
    ref = Reference(title=body.title, authors=body.authors, year=body.year, doi=body.doi, arxiv_id=body.arxiv_id,
                    text=body.text)
    if not (ref.title or ref.doi or ref.arxiv_id):
        if body.text.strip():
            ref = Reference(title=body.text.strip())
        else:
            raise HTTPException(400, "Give a DOI, an arXiv ID or a title")
    result = await ProjectLiterature(campaign_id).add(ref, by=_who())
    key = library.upsert_paper(result.paper)["key"] if result.paper else ""
    return {"check": result.to_dict(), "added": result.verified, "key": key, "literature": _literature(campaign_id)}


class AddKnown(BaseModel):
    paper_ids: list[str]
    search_id: str = ""


@project_router.post(L + "/papers/add")
async def add_known(campaign_id: str, body: AddKnown) -> dict[str, Any]:
    """Add papers already found (from a search) to the project's literature."""
    _project(campaign_id)
    added = 0
    for pid in body.paper_ids:
        if library.paper(pid) is None:
            continue
        added += library.add_item(campaign_id, pid, source="search", added_by=_who(), search_id=body.search_id)
    return {"added": added, "literature": _literature(campaign_id)}


class PaperChange(BaseModel):
    starred: bool | None = None


@project_router.put(L + "/papers/{paper_id}")
async def change_paper(campaign_id: str, paper_id: str, body: PaperChange) -> dict[str, Any]:
    if body.starred is not None:
        library.star(campaign_id, paper_id, body.starred)
    return _literature(campaign_id)


@project_router.delete(L + "/papers/{paper_id}")
async def remove_paper(campaign_id: str, paper_id: str) -> dict[str, Any]:
    """Take a paper out of the project's literature (where it was used stays recorded)."""
    if not library.remove_item(campaign_id, paper_id):
        raise HTTPException(404, "The paper is not in this project's literature")
    return _literature(campaign_id)


class RefDecision(BaseModel):
    action: str             # replace (with the closest real paper, or paper_id) | remove | have
    paper_id: str = ""


@project_router.post(L + "/refs/{ref_id}")
async def decide_ref(campaign_id: str, ref_id: str, body: RefDecision) -> dict[str, Any]:
    """What to do about a reference that could not be verified."""
    ref = library.ref(ref_id)
    if ref is None or ref["campaign_id"] != campaign_id:
        raise HTTPException(404, "No such reference")
    who = _who()
    if body.action == "replace":
        pid = body.paper_id
        if not pid and ref["closest"]:
            pid = library.upsert_paper(Paper.from_dict(ref["closest"]))["paper_id"]
        if not pid or library.paper(pid) is None:
            raise HTTPException(400, "There is no real paper to use instead; add the right one by DOI or title")
        library.resolve_ref(ref_id, "replaced", decided_by=who, paper_id=pid)
        library.add_item(campaign_id, pid, source="person", added_by=who)
    elif body.action == "remove":
        library.resolve_ref(ref_id, "removed", decided_by=who)
    elif body.action == "have":
        library.resolve_ref(ref_id, "have", decided_by=who)
    elif body.action == "reopen":
        library.resolve_ref(ref_id, "", decided_by="")
    else:
        raise HTTPException(400, "action must be replace, remove, have or reopen")
    return _literature(campaign_id)


class Resolve(BaseModel):
    keys: list[str] = []          # [Key] citations
    refs: list[str] = []          # [ref: ...] citations, the text inside the brackets


@project_router.post(L + "/resolve")
async def resolve_citations(campaign_id: str, body: Resolve) -> dict[str, Any]:
    """What the citations in a text point to: for each key, the paper; for each [ref: ...], whether it
    was found, what it was replaced with, or the nearest real paper."""
    from aurora.literature.check import parse_ref_tag
    from aurora.literature.records import title_similarity
    _project(campaign_id)
    known = library.known_to(campaign_id)
    in_project = {i["paper_id"] for i in library.items(campaign_id)}
    uses, _ = _uses_by(campaign_id)
    pdfs = _pdfs(campaign_id)

    def view(entry: dict[str, Any]) -> dict[str, Any]:
        pid = entry["paper_id"]
        return {**paper_view(entry), "status": "verified", "in_project": pid in in_project,
                "used_in": uses.get(pid, []), "pdf_file": pdfs.get(entry["key"], "")}

    keys: dict[str, Any] = {}
    for k in body.keys[:200]:
        entry = library.paper(k)
        keys[k] = view(entry) if entry and entry["paper_id"] in known else {"status": "unknown", "key": k}
    refs: dict[str, Any] = {}
    for raw in body.refs[:100]:
        ref = parse_ref_tag(raw)
        out: dict[str, Any] = {"label": ref.title or ref.label(), "status": "unchecked"}
        row = library.known_ref(campaign_id, ref.to_dict())
        if row is not None:
            out.update(status=row["status"], ref_id=row["ref_id"], reason=row["reason"], resolution=row["resolution"])
            if row["resolution"] == "replaced" and row["paper_id"] and (e := library.paper(row["paper_id"])):
                out["replaced_with"] = view(e)
            if row["closest"]:
                c = library.upsert_paper(Paper.from_dict(row["closest"]))
                out["closest"] = view(c)
        else:   # verified when it was checked: the paper it named
            for pid in known:
                e = library.paper(pid)
                if e is None:
                    continue
                p = e["paper"]
                if (ref.doi and p.doi == ref.doi) or (ref.arxiv_id and p.arxiv_id == ref.arxiv_id) or \
                        (ref.title and title_similarity(ref.title, p.title) >= 0.93):
                    out = {**view(e), "label": ref.title or ref.label()}
                    break
        refs[raw] = out
    return {"keys": keys, "refs": refs}


@project_router.post(L + "/refs/{ref_id}/recheck")
async def recheck_ref(campaign_id: str, ref_id: str) -> dict[str, Any]:
    """Check a flagged reference again (an index was down, or it was just published)."""
    ref = library.ref(ref_id)
    if ref is None or ref["campaign_id"] != campaign_id:
        raise HTTPException(404, "No such reference")
    r = ref["reference"]
    lit = ProjectLiterature(campaign_id)
    result = await lit.check_reference(Reference(title=r.get("title", ""), authors=r.get("authors", ""),
                                                 year=r.get("year"), doi=r.get("doi", ""),
                                                 arxiv_id=r.get("arxiv_id", ""), text=r.get("text", "")))
    if result.verified and result.paper is not None:
        pid = library.upsert_paper(result.paper)["paper_id"]
        library.resolve_ref(ref_id, "replaced", decided_by="the indexes (checked again)", paper_id=pid)
        library.add_item(campaign_id, pid, source="agent", added_by=ref["by"])
    else:
        library.record_ref(campaign_id, result, by=ref["by"], run_id=ref["run_id"])
    return {"status": result.status, "literature": _literature(campaign_id)}


class BibImport(BaseModel):
    bibtex: str           # BibTeX or RIS


@project_router.post(L + "/import")
async def import_bibtex(campaign_id: str, body: BibImport) -> dict[str, Any]:
    """Import a .bib or .ris file: every entry is checked; the ones found go in, the others are listed."""
    _project(campaign_id)
    entries = parse_any(body.bibtex)
    if not entries:
        raise HTTPException(400, "No BibTeX or RIS entries found")
    if len(entries) > 500:
        raise HTTPException(400, "At most 500 entries at a time")
    lit = ProjectLiterature(campaign_id)
    report = []
    for cite_key, ref in entries:
        result = await lit.add(ref, by=_who(), source="bibtex")
        report.append({"cite_key": cite_key, "status": result.status, "reason": result.reason,
                       "key": library.upsert_paper(result.paper)["key"] if result.paper else "",
                       "title": result.paper.title if result.paper else ref.title})
    return {"entries": report, "added": sum(r["status"] == "verified" for r in report),
            "literature": _literature(campaign_id)}


class Many(BaseModel):
    text: str             # DOIs, arXiv IDs or links, one per line (or separated by spaces or commas)


@project_router.post(L + "/papers/many")
async def add_many(campaign_id: str, body: Many) -> dict[str, Any]:
    """Add papers by pasting their DOIs or arXiv IDs; each is checked."""
    _project(campaign_id)
    items = [x.strip().strip(".,;") for x in re.split(r"[\s,;]+", body.text) if x.strip()]
    items = [x for x in items if clean_doi(x) or clean_arxiv(x) or "arxiv.org" in x][:200]
    if not items:
        raise HTTPException(400, "No DOI or arXiv ID found")
    lit = ProjectLiterature(campaign_id)
    report = []
    for x in items:
        result = await lit.add(Reference(text=x), by=_who())
        report.append({"cite_key": x, "status": result.status, "reason": result.reason,
                       "key": library.upsert_paper(result.paper)["key"] if result.paper else "",
                       "title": result.paper.title if result.paper else ""})
    return {"entries": report, "added": sum(r["status"] == "verified" for r in report), "literature": _literature(campaign_id)}


@project_router.post(L + "/upload")
async def upload_pdf(campaign_id: str, request: Request, name: str = "paper.pdf") -> dict[str, Any]:
    """Upload a PDF: it is recognised (DOI, arXiv ID or title), checked, and kept in the project's papers/."""
    _project(campaign_id)
    data = await request.body()
    if not data.startswith(b"%PDF"):
        raise HTTPException(400, "That is not a PDF")
    if len(data) > 80 * 1024 * 1024:
        raise HTTPException(400, "The PDF is larger than 80 MB")
    from aurora.api.routes.files import project_files_dir
    from aurora.literature.pdfs import identify
    ref = identify(data, name)
    lit = ProjectLiterature(campaign_id)
    result = await lit.add(ref, by=_who(), source="upload")
    folder = project_files_dir(campaign_id) / "papers"
    folder.mkdir(parents=True, exist_ok=True)
    if result.verified and result.paper is not None:
        key = library.upsert_paper(result.paper)["key"]
        target = folder / f"{key}.pdf"
    else:
        # kept as the person's own paper (vouched for), under its file name
        stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", re.sub(r"\.pdf$", "", name, flags=re.I))[:80] or "paper"
        target = folder / f"{stem}.pdf"
        rid = library.record_ref(campaign_id, result, by=_who())
        library.resolve_ref(rid, "have", decided_by=f"{_who()} (uploaded the PDF)")
        key = ""
    target.write_bytes(data)
    return {"check": result.to_dict(), "added": result.verified, "key": key, "file": f"papers/{target.name}",
            "recognised_as": ref.to_dict(), "literature": _literature(campaign_id)}


class Rule(BaseModel):
    add_found: str        # all | cited | none | '' (the console's default)


@project_router.put(L + "/rule")
async def set_rule(campaign_id: str, body: Rule) -> dict[str, Any]:
    """The project's own rule for the papers agents find (runs can still choose their own)."""
    _project(campaign_id)
    if body.add_found not in ("", *IMPORT_RULES):
        raise HTTPException(400, "add_found must be all, cited, none or empty")
    library.set_project_rule(campaign_id, body.add_found)
    return _literature(campaign_id)


class Version(BaseModel):
    version: int          # the arXiv version to cite (0: the latest)


@project_router.put(L + "/papers/{paper_id}/version")
async def cite_version(campaign_id: str, paper_id: str, body: Version) -> dict[str, Any]:
    entry = library.paper(paper_id)
    if entry is None or not library.has_item(campaign_id, paper_id):
        raise HTTPException(404, "The paper is not in this project's literature")
    library.set_cited_version(campaign_id, paper_id, body.version or entry["paper"].arxiv_version)
    return _literature(campaign_id)


@project_router.get(L + "/export.bib", response_class=PlainTextResponse)
async def export_bibtex(campaign_id: str) -> PlainTextResponse:
    _project(campaign_id)
    from aurora.literature.zotero import zotero_store
    items = library.items(campaign_id)
    stored = library.papers([i["paper_id"] for i in items])
    citekeys = zotero_store.citekeys(list(stored))       # Better BibTeX keys, where the papers have them
    entries = [{**stored[i["paper_id"]], "key": citekeys.get(i["paper_id"], stored[i["paper_id"]]["key"])}
               for i in items if i["paper_id"] in stored]
    return PlainTextResponse(to_bibtex(entries),
                             media_type="application/x-bibtex",
                             headers={"Content-Disposition": f'attachment; filename="{campaign_id}.bib"'})


# ---------------------------------------------------------------------------
# Your Library: your own papers, and the projects' papers you can see


@library_router.get("")
async def get_library() -> dict[str, Any]:
    from aurora.api.routes.campaigns import _ensure_campaign, store
    me = current_user()
    projects = {}
    for cid in visible(me, [row["campaign_id"] for row in store.list_campaigns()]):
        try:
            projects[cid] = _ensure_campaign(cid).name
        except Exception:
            continue
    papers: dict[str, dict[str, Any]] = {}

    def add(item: dict[str, Any], where: str) -> None:
        entry = library.paper(item["paper_id"])
        if entry is None:
            return
        row = papers.setdefault(item["paper_id"], {**paper_view(entry), "projects": [], "mine": False,
                                                   "added_at": item["added_at"]})
        if where:
            row["projects"].append({"campaign_id": where, "name": projects[where]})
        else:
            row["mine"] = True
        row["added_at"] = max(row["added_at"], item["added_at"])

    for item in library.items("", owner=me.user_id):
        add(item, "")
    for cid in projects:
        for item in library.items(cid):
            add(item, cid)
    from aurora.literature.zotero import zotero_store
    in_zotero = zotero_store.papers_in(me.user_id)
    for pid, row in papers.items():
        row["in_zotero"] = pid in in_zotero
    rows = sorted(papers.values(), key=lambda r: -r["added_at"])
    return {"papers": rows, "projects": [{"campaign_id": k, "name": v} for k, v in projects.items()],
            "rule": _rule(), "rules": list(IMPORT_RULES)}


@library_router.post("/search")
async def search_library(body: SearchBody) -> dict[str, Any]:
    """Search the indexes (your searches are kept in your own library's history)."""
    if not body.query.strip():
        raise HTTPException(400, "Type something to search for")
    me = current_user()
    lit = ProjectLiterature(f"user:{me.user_id}", rule="none")
    await lit.search(body.query.strip(), by=_who(), context="Library search", limit=min(body.limit, 25))
    view = _search_view(library.search(lit.last_search_id), {i["paper_id"] for i in library.items("", owner=me.user_id)})
    for row in view["papers"]:
        row["projects"] = library.projects_of(row["paper_id"])
    return view


@library_router.post("/papers")
async def add_to_library(body: AddBody) -> dict[str, Any]:
    """Add a paper to your own library (checked first)."""
    ref = Reference(title=body.title or ("" if (body.doi or body.arxiv_id) else body.text.strip()), authors=body.authors,
                    year=body.year, doi=body.doi, arxiv_id=body.arxiv_id, text=body.text)
    me = current_user()
    result = await ProjectLiterature("").add(ref, by=_who(), owner=me.user_id)
    return {"check": result.to_dict(), "added": result.verified,
            "key": library.upsert_paper(result.paper)["key"] if result.paper else ""}


class SaveBody(BaseModel):
    paper_id: str
    campaign_id: str = ""        # '' : your own library


@library_router.post("/save")
async def save_paper(body: SaveBody) -> dict[str, Any]:
    """Save a known paper to your own library or to a project (you need edit rights there)."""
    if library.paper(body.paper_id) is None:
        raise HTTPException(404, "No such paper")
    me = current_user()
    if body.campaign_id:
        require(body.campaign_id, "edit", me)
        added = library.add_item(body.campaign_id, body.paper_id, source="person", added_by=_who())
    else:
        added = library.add_item("", body.paper_id, source="person", added_by=_who(), owner=me.user_id)
    return {"added": added, "at": time.time()}


@library_router.delete("/papers/{paper_id}")
async def remove_from_library(paper_id: str) -> dict[str, Any]:
    if not library.remove_item("", paper_id, owner=current_user().user_id):
        raise HTTPException(404, "The paper is not in your library")
    return {"ok": True}
