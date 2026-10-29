"""The feed, the people and sources you follow, and the daily digest: ``/api/feed``.

Everything here is the asking person's own.  Saving an item to a project, bringing it to a
project's brainstorm or giving it to a project's front desk checks the rights there.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from aurora.auth.guard import current_user, require, rights
from aurora.feed import people as ppl
from aurora.feed import service as svc
from aurora.feed.sources import SOURCE_NAMES, SourceError, sources
from aurora.feed.store import WANTS, feed_store

router = APIRouter(prefix="/api/feed", tags=["feed"])

KIND_LABEL = {"paper": "Paper", "post": "Post", "blog": "Blog", "code": "Code", "video": "Video", "discussion": "Discussion"}


def _uid() -> str:
    return current_user().user_id


def _who() -> str:
    u = current_user()
    return "You" if u.is_local else u.name


def _projects() -> dict[str, str]:
    return {p["campaign_id"]: p["name"] for p in _profiles()}


def _profiles() -> list[dict[str, Any]]:
    from aurora.api.routes.campaigns import _ensure_campaign, store
    from aurora.auth.guard import visible
    out = []
    for cid in visible(current_user(), [r["campaign_id"] for r in store.list_campaigns()]):
        try:
            out.append({"campaign_id": cid, "name": _ensure_campaign(cid).name})
        except Exception:
            continue
    return out


def _item(item_id: str) -> dict[str, Any]:
    it = feed_store.item(item_id)
    if it is None or it["user_id"] != _uid():
        raise HTTPException(404, "No such item")
    return it


def _open_label(it: dict[str, Any]) -> str:
    if it["kind"] == "paper":
        return "PDF" if it["pdf_url"] else "Open"
    if it["kind"] == "post":
        return "Open thread" if "repl" in (it["extra"] or "") else "Open"
    if it["kind"] == "blog":
        return "Read"
    if it["kind"] == "code":
        return "Open on GitHub"
    if it["kind"] == "video":
        return "Watch"
    return "See all replies"


def item_view(it: dict[str, Any], ranks: list[dict[str, Any]], projects: dict[str, str],
              saves: dict[str, dict[str, Any]]) -> dict[str, Any]:
    fits = svc.visible_rank(ranks, projects)
    best = fits[0] if fits else None
    saved = saves.get(it["item_id"])
    return {
        "item_id": it["item_id"], "kind": it["kind"], "label": KIND_LABEL[it["kind"]], "source": it["source"],
        "src": it["src"], "url": it["url"], "pdf_url": it["pdf_url"], "open": _open_label(it), "title": it["title"],
        "summary": it["summary"], "points": it["points"], "places": it["places"], "people": it["people"],
        "idv": it["idv"], "extra": it["extra"], "published_at": it["published_at"] or None, "fetched_at": it["fetched_at"],
        "rel": best["rel"] if best else 1, "why": best["why"] if best else "", "campaign_id": best["campaign_id"] if best else "",
        "project": best["project"] if best else "", "fits": fits, "folded": not best or best["rel"] <= 2,
        "saved": ({"campaign_id": saved["campaign_id"], "project": projects.get(saved["campaign_id"], "")}
                  if saved else None),
        "is_paper": bool(it["paper"]), "about": it["about"],
    }


def _initials(name: str) -> str:
    return "".join(w[0] for w in name.replace("@", "").split() if w)[:2].upper() or "?"


def _person_view(p: dict[str, Any], *, with_activity: bool = True) -> dict[str, Any]:
    out = {"person_id": p["person_id"], "name": p["name"], "initials": _initials(p["name"]), "aff": p["aff"],
           "ids": p["ids"], "wants": {w: bool(p["wants"].get(w, True)) for w in WANTS}, "coauthors": p["coauthors"]}
    if with_activity:
        out["activity"], out["fresh"] = svc.activity(_uid(), p)
    return out


def _channels() -> list[dict[str, str]]:
    from aurora.api.routes.campaigns import live_notifications
    try:
        chans = live_notifications(_uid()).channels
    except Exception:
        return []
    kinds = {"wecom": "WeCom", "feishu": "Feishu", "slack": "Slack", "serverchan": "Server酱", "webhook": "webhook"}
    return [{"id": c.id or c.name, "label": f"{c.name or kinds[c.type]} ({kinds[c.type]})"} for c in chans if c.enabled]


def _digest_view(cfg: dict[str, Any]) -> dict[str, Any]:
    chans = _channels()
    d = cfg["digest"]
    label = next((c["label"] for c in chans if c["id"] == d.get("to")), "Only in the console")
    return {**d, "to": d.get("to") if label != "Only in the console" else "console", "label": label, "channels": chans}


def _spaces() -> list[dict[str, str]]:
    """Where an item can be brought: the projects you may write in, and your own space."""
    out = [{"space": p["campaign_id"], "name": p["name"]} for p in _profiles()
           if "comment" in rights(current_user(), p["campaign_id"])]
    return [*out, {"space": "global", "name": "Your own ideas"}]


# ---------------------------------------------------------------------------- the feed

@router.get("")
async def get_feed() -> dict[str, Any]:
    from aurora.auth.store import auth_store
    me = current_user()
    first = not auth_store.get_pref(me.user_id, "feed_used")
    if first:
        auth_store.set_pref(me.user_id, "feed_used", True)
    st = svc.status(me.user_id)
    if not st.get("last") and not svc.running(me.user_id):
        svc.start_refresh(me)
    projects = _projects()
    rows = feed_store.items(me.user_id, time.time() - svc.SHOW_DAYS * 86400)
    rows = [r for r in rows if r["ranked"] and not svc.is_old(r)]
    ranks = feed_store.ranks([r["item_id"] for r in rows])
    saves = feed_store.saves(me.user_id)
    items = [item_view(r, ranks.get(r["item_id"], []), projects, saves) for r in rows]
    items.sort(key=lambda x: (-x["rel"], -(x["published_at"] or x["fetched_at"])))
    cfg = svc.settings(me.user_id)
    day = datetime.now().strftime("%Y-%m-%d")
    counts = st.get("counts", {}) if st.get("date") == day else {}
    return {
        "date": time.time(), "items": items, "projects": [{"campaign_id": k, "name": v} for k, v in projects.items()],
        "spaces": _spaces(), "people": [_person_view(p) for p in feed_store.people(me.user_id)],
        "digest": _digest_view(cfg), "counts": counts, "problems": st.get("problems", {}), "last": st.get("last"),
        "refreshing": svc.running(me.user_id), "saved_count": len(saves), "sources": SOURCE_NAMES,
        "following_anything": bool(cfg["topics"] or cfg["keywords"] or cfg["rss"] or feed_store.people(me.user_id)
                                   or (cfg["from_objectives"] and projects) or cfg["hf"]),
    }


@router.post("/refresh")
async def refresh_feed() -> dict[str, Any]:
    started = svc.start_refresh(current_user())
    return {"refreshing": True, "started": started}


@router.get("/saved")
async def saved_items() -> dict[str, Any]:
    me = current_user()
    projects = _projects()
    saves = feed_store.saves(me.user_id)
    rows = [r for r in (feed_store.item(i) for i in saves) if r]
    ranks = feed_store.ranks([r["item_id"] for r in rows])
    items = [item_view(r, ranks.get(r["item_id"], []), projects, saves) for r in rows]
    items.sort(key=lambda x: -saves[x["item_id"]]["saved_at"])
    return {"items": items}


def _paper_into(campaign_id: str, it: dict[str, Any], source: str = "feed") -> str:
    """Put a feed item's paper into a project's literature (or your own library); its paper ID."""
    from aurora.literature.library import library
    from aurora.literature.records import Paper
    entry = library.upsert_paper(Paper.from_dict(it["paper"]))
    if campaign_id:
        library.add_item(campaign_id, entry["paper_id"], source=source, added_by=_who())
    else:
        library.add_item("", entry["paper_id"], source=source, added_by=_who(), owner=_uid())
    return entry["paper_id"]


class SaveBody(BaseModel):
    campaign_id: str = ""            # '' : save for later (papers go to your own library)


@router.post("/items/{item_id}/save")
async def save_item(item_id: str, body: SaveBody) -> dict[str, Any]:
    it = _item(item_id)
    name = "Saved for later"
    if body.campaign_id:
        require(body.campaign_id, "edit")
        name = _projects().get(body.campaign_id, "")
    if it["paper"]:
        _paper_into(body.campaign_id, it)
    feed_store.save(_uid(), item_id, body.campaign_id)
    return {"saved": {"campaign_id": body.campaign_id, "project": name if body.campaign_id else ""},
            "in_literature": bool(it["paper"] and body.campaign_id)}


@router.delete("/items/{item_id}/save")
async def unsave_item(item_id: str) -> dict[str, Any]:
    _item(item_id)
    feed_store.unsave(_uid(), item_id)
    return {"ok": True}


@router.post("/items/{item_id}/hide")
async def hide_item(item_id: str) -> dict[str, Any]:
    """Not interested: hidden, and similar items rank lower from now on."""
    _item(item_id)
    feed_store.update_item(item_id, hidden=1)
    return {"ok": True}


@router.delete("/items/{item_id}/hide")
async def unhide_item(item_id: str) -> dict[str, Any]:
    _item(item_id)
    feed_store.update_item(item_id, hidden=0)
    return {"ok": True}


class BringBody(BaseModel):
    space: str                       # a project ID, or 'global' for your own ideas


@router.post("/items/{item_id}/bring")
async def bring_item(item_id: str, body: BringBody) -> dict[str, Any]:
    """Bring an item to a brainstorm space as material (a paper, or a post, blog or discussion)."""
    from aurora.api.routes import brainstorm as bs
    from aurora.brainstorm.store import bs_store
    it = _item(item_id)
    key, cid = bs._space(body.space)
    bs._may_write(cid)
    paper_id = ""
    if it["paper"]:
        paper_id = _paper_into(cid if cid and "edit" in rights(current_user(), cid) else "", it)
    card = bs_store.add_card(key, _uid(), "material", it["title"], by=_uid(), vis="private", paper_id=paper_id,
                             source=f"{KIND_LABEL[it['kind']]} · {it['source']}", url=it["url"])
    fits = svc.visible_rank(feed_store.ranks([item_id])[item_id], _projects())
    why = next((f["why"] for f in fits if f["campaign_id"] == cid), fits[0]["why"] if fits else "")
    if bs._activity() != "off" and why:
        bs_store.add_message(f"card:{card['card_id']}", "Librarian", f"Brought in from the Library. {why}")
    space_name = next((s["name"] for s in _spaces() if s["space"] == body.space), "")
    return {"card": bs._card_view(bs_store.card(card["card_id"])), "space": body.space, "space_name": space_name}


class AskBody(BaseModel):
    campaign_id: str


@router.post("/items/{item_id}/ask")
async def ask_front_desk(item_id: str, body: AskBody) -> dict[str, Any]:
    """Give an item to a project's front desk: it goes with your next message there.
    A paper joins the project's literature, marked as a seed."""
    from aurora.literature.library import library
    it = _item(item_id)
    require(body.campaign_id, "view")
    paper_id = ""
    if it["paper"] and "edit" in rights(current_user(), body.campaign_id):
        paper_id = _paper_into(body.campaign_id, it)
        library.record_use(body.campaign_id, "seed", paper_id=paper_id, by=_who())
    if not feed_store.saves(_uid()).get(item_id) and "edit" in rights(current_user(), body.campaign_id):
        feed_store.save(_uid(), item_id, body.campaign_id)
    return {"campaign_id": body.campaign_id, "ref": library_ref(it), "seed": bool(paper_id)}


def library_ref(it: dict[str, Any]) -> dict[str, Any]:
    """An item as it is attached to a message."""
    return {"kind": "feed", "item_id": it["item_id"], "title": it["title"], "label": KIND_LABEL[it["kind"]],
            "ok": bool(it["idv"]), "by": (it["people"][0][0] if it["people"] else "")}


def ref_text(ref: dict[str, Any], user_id: str) -> str | None:
    """What the front desk reads of an attached Library item (a feed item or a paper)."""
    if ref.get("kind") == "feed":
        it = feed_store.item(str(ref.get("item_id", "")))
        if it is None or it["user_id"] != user_id:
            return None
        lines = [f"{KIND_LABEL[it['kind']]}: {it['title']}", f"From: {it['source']}" + (f" · {it['idv']}" if it["idv"] else "")]
        if it["people"]:
            lines.append("By: " + ", ".join(n for n, _ in it["people"][:8]))
        if it["url"]:
            lines.append(f"Link: {it['url']}")
        if it["summary"]:
            lines.append(f"Summary: {it['summary']}")
        for p in it["points"]:
            lines.append(f"- {p}")
        ranks = feed_store.ranks([it["item_id"]])[it["item_id"]]
        if ranks:
            lines.append(f"Why the feed picked it: {ranks[0]['why']}")
        if it["paper"]:
            from aurora.literature.library import library
            from aurora.literature.records import Paper
            entry = library.upsert_paper(Paper.from_dict(it["paper"]))
            lines.append(f"Its key in the literature: [{entry['key']}]")
        return "\n".join(lines)
    if ref.get("kind") == "paper":
        from aurora.literature.library import library
        entry = library.paper(str(ref.get("paper_id", "")))
        if entry is None:
            return None
        p = entry["paper"]
        return (f"Paper [{entry['key']}] (its key in the literature): {p.title}\nBy: {p.byline()}\n"
                + (f"Abstract: {p.abstract[:2500]}" if p.abstract else ""))
    return None


@router.get("/library")
async def attachable() -> dict[str, Any]:
    """What can be attached from the Library: what you saved from the feed, and your papers."""
    from aurora.literature.library import library
    me = current_user()
    projects = _projects()
    saves = feed_store.saves(me.user_id)
    out = []
    for iid, s in saves.items():
        it = feed_store.item(iid)
        if it:
            out.append({**library_ref(it), "where": projects.get(s["campaign_id"], "Saved for later")})
    for row in library.items("", owner=me.user_id):
        entry = library.paper(row["paper_id"])
        if entry:
            out.append({"kind": "paper", "paper_id": row["paper_id"], "title": entry["paper"].title, "label": "Paper",
                        "ok": True, "by": entry["paper"].byline(), "where": "Your library"})
    return {"items": out}


# ---------------------------------------------------------------------------- following

@router.get("/following")
async def following() -> dict[str, Any]:
    from aurora.auth.store import auth_store
    me = current_user()
    cfg = svc.settings(me.user_id)
    topics = auth_store.get_pref(me.user_id, "feed_topics") or {}
    projects = _projects()
    derived = [{"project": projects[cid], "keywords": t.get("keywords", []), "categories": t.get("categories", [])}
               for cid, t in topics.items() if cid in projects]
    return {"people": [_person_view(p) for p in feed_store.people(me.user_id)], "topics": cfg["topics"],
            "keywords": cfg["keywords"], "from_objectives": cfg["from_objectives"], "derived": derived, "hf": cfg["hf"],
            "discussions": cfg["discussions"], "rss": cfg["rss"], "digest": _digest_view(cfg),
            "x_key": bool(svc.x_key(me.user_id))}


class Settings(BaseModel):
    topics: list[str] | None = None
    keywords: list[str] | None = None
    from_objectives: bool | None = None
    hf: bool | None = None
    discussions: bool | None = None
    digest: dict[str, Any] | None = None


@router.put("/settings")
async def put_settings(body: Settings) -> dict[str, Any]:
    from aurora.auth.store import auth_store
    me = current_user()
    saved = auth_store.get_pref(me.user_id, "feed") or {}
    change = body.model_dump(exclude_none=True)
    if "topics" in change:
        change["topics"] = list(dict.fromkeys(t.strip() for t in change["topics"] if t.strip()))[:20]
    if "keywords" in change:
        change["keywords"] = list(dict.fromkeys(k.strip() for k in change["keywords"] if k.strip()))[:20]
    if "digest" in change:
        d = {**svc.settings(me.user_id)["digest"], **change["digest"]}
        at = str(d.get("at", "08:30")).strip()
        try:
            h, m = (int(x) for x in at.split(":"))
            assert 0 <= h < 24 and 0 <= m < 60
        except (ValueError, AssertionError):
            raise HTTPException(400, "The time looks like 08:30") from None
        d["at"] = f"{h:02d}:{m:02d}"
        try:
            d["top"] = max(1, min(20, int(d.get("top", 5))))
        except (TypeError, ValueError):
            raise HTTPException(400, "How many per project: a number from 1 to 20") from None
        if d.get("to") != "console" and d.get("to") not in {c["id"] for c in _channels()}:
            raise HTTPException(400, "Choose a notification channel set up under Settings → Notifications")
        d["skip"] = bool(d.get("skip", True))
        change["digest"] = {k: d[k] for k in ("to", "at", "top", "skip")}
    if {"topics", "keywords", "from_objectives", "hf"} & set(change):
        auth_store.set_pref(me.user_id, "feed_status", {**svc.status(me.user_id), "last": 0})   # fetch again soon
    svc.save_settings(me.user_id, {**saved, **change})
    return await following()


class RssBody(BaseModel):
    url: str


@router.post("/rss")
async def add_rss(body: RssBody) -> dict[str, Any]:
    """Follow a blog or newsletter: its feed is looked up from the page if needed."""
    from aurora.auth.store import auth_store
    me = current_user()
    try:
        url, title = await sources().discover_feed(body.url.strip())
    except SourceError as exc:
        raise HTTPException(400, str(exc)) from exc
    cfg = svc.settings(me.user_id)
    if any(f["url"] == url for f in cfg["rss"]):
        raise HTTPException(409, "You already follow this feed")
    cfg["rss"].append({"url": url, "title": title or url, "state": "ok", "last_ok": None, "error": ""})
    svc.save_settings(me.user_id, {**(auth_store.get_pref(me.user_id, "feed") or {}), "rss": cfg["rss"]})
    return await following()


@router.delete("/rss")
async def remove_rss(url: str) -> dict[str, Any]:
    from aurora.auth.store import auth_store
    me = current_user()
    cfg = svc.settings(me.user_id)
    rest = [f for f in cfg["rss"] if f["url"] != url]
    if len(rest) == len(cfg["rss"]):
        raise HTTPException(404, "You do not follow this feed")
    svc.save_settings(me.user_id, {**(auth_store.get_pref(me.user_id, "feed") or {}), "rss": rest})
    return await following()


class KeyBody(BaseModel):
    key: str


@router.put("/x-key")
async def put_x_key(body: KeyBody) -> dict[str, Any]:
    from aurora.auth.secrets import put_key
    if len(body.key.strip()) < 20:
        raise HTTPException(400, "An X API key (bearer token) is longer than that")
    try:
        await sources().json("https://api.x.com/2/users/by/username/X", headers={"Authorization": f"Bearer {body.key.strip()}"},
                             what="X")
    except SourceError as exc:
        raise HTTPException(400, f"X did not accept the key: {exc}") from exc
    put_key(_uid(), "x", body.key.strip())
    _update_x_states(True)
    return await following()


@router.delete("/x-key")
async def delete_x_key() -> dict[str, Any]:
    from aurora.auth.store import auth_store
    auth_store.delete_secret(_uid(), "x")
    _update_x_states(False)
    return await following()


def _update_x_states(have: bool) -> None:
    for p in feed_store.people(_uid()):
        ids = [ppl.x_identity(i["handle"], have, follow=have) if i["kind"] == "x" else i for i in p["ids"]]
        feed_store.update_person(p["person_id"], ids=ids)


@router.post("/digest/test")
async def test_digest() -> dict[str, Any]:
    """Send today's digest now, to see how it looks."""
    error = await svc.send_digest(current_user())
    if error:
        raise HTTPException(400, error)
    return {"ok": True}


# ---------------------------------------------------------------------------- people

class FindBody(BaseModel):
    query: str


@router.post("/people/find")
async def find_people(body: FindBody) -> dict[str, Any]:
    if not body.query.strip():
        raise HTTPException(400, "Type a name, a profile link, an ORCID or a handle")
    try:
        found = await ppl.find(sources(), body.query, have_x_key=bool(svc.x_key(_uid())),
                               access=svc._access_for(current_user()))
    except SourceError as exc:
        raise HTTPException(502, f"Could not look them up: {exc}") from exc
    return {"candidates": found}


@router.post("/people/find/stream")
async def find_people_stream(body: FindBody) -> StreamingResponse:
    """The same search, told as it goes: one JSON line per step ({"step"}), then {"candidates"} or {"error"}."""
    if not body.query.strip():
        raise HTTPException(400, "Type a name, a profile link, an ORCID or a handle")
    have_x_key, access = bool(svc.x_key(_uid())), svc._access_for(current_user())
    steps: asyncio.Queue[str] = asyncio.Queue()

    async def lines():
        task = asyncio.create_task(ppl.find(sources(), body.query, have_x_key=have_x_key, access=access,
                                            say=steps.put_nowait))
        try:
            while not (task.done() and steps.empty()):
                getter = asyncio.ensure_future(steps.get())
                await asyncio.wait({getter, task}, return_when=asyncio.FIRST_COMPLETED)
                if getter.done():
                    yield json.dumps({"step": getter.result()}) + "\n"
                else:
                    getter.cancel()
            try:
                yield json.dumps({"candidates": task.result()}) + "\n"
            except SourceError as exc:
                yield json.dumps({"error": f"Could not look them up: {exc}"}) + "\n"
        finally:
            task.cancel()                     # the page was closed: stop searching
    return StreamingResponse(lines(), media_type="application/x-ndjson")


class Candidate(BaseModel):
    candidate: dict[str, Any]


@router.post("/people/identities")
async def find_identities(body: Candidate) -> dict[str, Any]:
    """Where else the person publishes and posts."""
    ids = await ppl.identities(sources(), body.candidate, have_x_key=bool(svc.x_key(_uid())))
    return {"ids": ids}


class FollowBody(BaseModel):
    candidate: dict[str, Any]
    ids: list[dict[str, Any]]


@router.post("/people")
async def follow_person(body: FollowBody) -> dict[str, Any]:
    from aurora.auth.store import auth_store
    me = current_user()
    c = body.candidate
    name = str(c.get("name", "")).strip()
    if not name:
        raise HTTPException(400, "Who?")
    keys = {(i.get("kind"), i.get("handle") or i.get("url")) for i in body.ids if i.get("follow")}
    for p in feed_store.people(me.user_id):
        if keys & {(i.get("kind"), i.get("handle") or i.get("url")) for i in p["ids"] if i.get("follow")}:
            raise HTTPException(409, f"You already follow {p['name']}")
    have_x = bool(svc.x_key(me.user_id))
    ids = []
    for i in body.ids:
        i = {k: i.get(k) for k in ("kind", "handle", "url", "label", "state", "follow", "note", "works") if k in i}
        if i.get("kind") == "x" and not have_x:
            i["follow"] = False
        if i.get("kind") not in ("openalex", "s2", "arxiv", "bluesky", "mastodon", "x", "blog", "github", "youtube",
                                 "linkedin", "link", "talk"):
            continue
        ids.append(i)
    for p in feed_store.people(me.user_id, removed=True):       # followed before: bring them back
        if p["name"] == name:
            feed_store.update_person(p["person_id"], removed=0, ids=ids, aff=c.get("aff", p["aff"]))
            person = feed_store.person(p["person_id"])
            break
    else:
        person = feed_store.add_person(me.user_id, name, aff=str(c.get("aff", "")), ids=ids,
                                       coauthors=[str(x) for x in c.get("coauthors") or []][:8])
    auth_store.set_pref(me.user_id, "feed_status", {**svc.status(me.user_id), "last": 0})
    svc.start_refresh(me)
    return {"person": _person_view(person)}  # type: ignore[arg-type]


def _person(person_id: str) -> dict[str, Any]:
    p = feed_store.person(person_id)
    if p is None or p["user_id"] != _uid():
        raise HTTPException(404, "You do not follow this person")
    return p


class PersonChange(BaseModel):
    wants: dict[str, bool] | None = None
    ids: list[dict[str, Any]] | None = None
    removed: bool | None = None


@router.patch("/people/{person_id}")
async def change_person(person_id: str, body: PersonChange) -> dict[str, Any]:
    p = _person(person_id)
    fields: dict[str, Any] = {}
    if body.wants is not None:
        fields["wants"] = {**p["wants"], **{k: bool(v) for k, v in body.wants.items() if k in WANTS}}
    if body.ids is not None:
        old = {(i["kind"], i.get("handle") or i.get("url")): i for i in p["ids"]}
        have_x = bool(svc.x_key(_uid()))
        new = []
        for i in body.ids:
            known = old.get((i.get("kind"), i.get("handle") or i.get("url")))
            if known:
                new.append({**known, "follow": bool(i.get("follow")) and (known["kind"] != "x" or have_x)
                            and known.get("state") != "muted"})
        fields["ids"] = new
    if body.removed is not None:
        fields["removed"] = int(body.removed)
    feed_store.update_person(person_id, **fields)
    return {"person": _person_view(feed_store.person(person_id))}  # type: ignore[arg-type]


@router.delete("/people/{person_id}")
async def unfollow(person_id: str) -> dict[str, Any]:
    _person(person_id)
    feed_store.update_person(person_id, removed=1)
    return {"ok": True}


@router.get("/people/{person_id}")
async def scholar(person_id: str) -> dict[str, Any]:
    """A followed person's page: what they published and posted, what others say about their
    work, and where their work is in your projects."""
    from aurora.api.routes.literature import paper_view
    from aurora.literature.library import library
    p = _person(person_id)
    feed_store.update_person(person_id, seen_at=time.time())
    projects = _projects()
    rows = feed_store.of_person(person_id)
    saves = feed_store.saves(_uid())
    timeline = []
    for r in rows:
        kind = "about" if r["about"] == person_id else r["kind"]
        in_project = ""
        s = saves.get(r["item_id"])
        if s and s["campaign_id"] in projects:
            in_project = f"Saved to {projects[s['campaign_id']]}"
        elif r["paper"]:
            ident = r["paper"].get("arxiv_id") or r["paper"].get("doi")
            for cid, name in projects.items():
                for it in library.items(cid):
                    e = library.paper(it["paper_id"])
                    if e and ident and ident in (e["paper"].arxiv_id, e["paper"].doi):
                        in_project = f"In {name}"
                        break
                if in_project:
                    break
        timeline.append({"item_id": r["item_id"], "k": kind, "label": "About their work" if kind == "about" else KIND_LABEL[r["kind"]],
                         "when": r["published_at"] or r["fetched_at"], "source": r["source"], "title": r["title"],
                         "body": r["summary"], "url": r["url"], "in_project": in_project, "points": r["points"]})
    # their work in your projects: papers with them as an author
    from aurora.feed.service import same_author
    theirs = []
    for cid, name in projects.items():
        uses = {}
        for u in library.uses(cid):
            uses.setdefault(u["paper_id"], []).append(u["place"])
        for it in library.items(cid):
            e = library.paper(it["paper_id"])
            if not e or not any(same_author(p["name"], a) for a in e["paper"].authors):
                continue
            how = {"feed": "saved from the feed", "agent": "cited by the agents", "zotero": "from Zotero",
                   "person": "added by hand", "search": "found in a search", "bibtex": "imported"}.get(it["source"], it["source"])
            places = sorted(set(uses.get(it["paper_id"], [])))
            theirs.append({**paper_view(e), "campaign_id": cid, "project": name,
                           "how": how + (f"; used in {', '.join(places)}" if places else ""),
                           "cited_version": it.get("cited_version") or 0})
    return {"person": _person_view(p, with_activity=False), "timeline": timeline, "theirs": theirs,
            "coauthors": p["coauthors"]}

