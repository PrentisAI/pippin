"""Gathering a person's feed, ranking it against their projects, and the daily digest.

:func:`refresh` asks every source the person follows (in their name, with their key for the
model), keeps the new items, gathers the discussions around their projects' papers and ranks
what is new.  :func:`loop` does this every few hours for everyone who uses the feed, and sends
each person's digest at the time they chose.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import Any

from aurora.feed import rank as rk
from aurora.feed.sources import SourceError, Sources, clip, paper_ids_in, sources
from aurora.feed.store import feed_store
from aurora.literature.records import Paper, family_name, fold

logger = logging.getLogger(__name__)

EVERY_HOURS = 3                  # how often each person's sources are asked
SHOW_DAYS = 3                    # the feed shows what came in over the last days
KEEP_DAYS = 30                   # items nobody saved are forgotten after this
OLD_DAYS = 14                    # older than this when first seen: only on the person's page, not in the feed
RANK_CALLS_PER_DAY = 12          # model calls for ranking, per person
BATCH = 15                       # items per ranking call
DISCUSSIONS_PER_REFRESH = 5

DEFAULTS: dict[str, Any] = {
    "topics": [],                 # arXiv categories
    "keywords": [],               # searched in arXiv and OpenAlex
    "from_objectives": True,      # topics from my projects' objectives
    "hf": True,                   # HF Daily Papers
    "discussions": True,          # discussions around papers in my projects
    "rss": [],                    # [{url, title, state, last_ok, error}]
    "digest": {"to": "console", "at": "08:30", "top": 5, "skip": True},
}

_running: dict[str, asyncio.Task] = {}


# ---------------------------------------------------------------------------- settings

def settings(user_id: str) -> dict[str, Any]:
    from aurora.auth.store import auth_store
    saved = auth_store.get_pref(user_id, "feed") or {}
    out = {**DEFAULTS, **{k: v for k, v in saved.items() if k in DEFAULTS}}
    out["digest"] = {**DEFAULTS["digest"], **(saved.get("digest") or {})}
    return out


def save_settings(user_id: str, value: dict[str, Any]) -> dict[str, Any]:
    from aurora.auth.store import auth_store
    auth_store.set_pref(user_id, "feed", value)
    return settings(user_id)


def status(user_id: str) -> dict[str, Any]:
    from aurora.auth.store import auth_store
    return auth_store.get_pref(user_id, "feed_status") or {}


def _set_status(user_id: str, **fields: Any) -> None:
    from aurora.auth.store import auth_store
    auth_store.set_pref(user_id, "feed_status", {**status(user_id), **fields})


def x_key(user_id: str) -> str:
    from aurora.auth.secrets import get_key
    return get_key(user_id, "x") or ""


def running(user_id: str) -> bool:
    t = _running.get(user_id)
    return t is not None and not t.done()


# ---------------------------------------------------------------------------- projects

def project_profiles(user: Any) -> list[dict[str, Any]]:
    """The projects a person can see, each described by its objective, papers and findings."""
    from aurora.api.routes.campaigns import _ensure_campaign, _report_path, _run_findings, store
    from aurora.auth.guard import visible
    from aurora.literature.library import library
    out = []
    for cid in visible(user, [r["campaign_id"] for r in store.list_campaigns()]):
        try:
            camp = _ensure_campaign(cid)
        except Exception:
            continue
        parts = [f"Objective: {camp.objective or camp.name}"]
        titles = []
        for it in library.items(cid)[-15:]:
            entry = library.paper(it["paper_id"])
            if entry:
                titles.append(entry["paper"].title)
        if titles:
            parts.append("Papers in its literature: " + "; ".join(titles))
        runs = sorted(camp.experiments.values(), key=lambda r: r.started_at)[-2:]
        for r in runs:
            lines = _run_findings(r.campaign_dir)[:6]
            if lines:
                parts.append(f"Run {r.run_id} ({r.task_name}) found:\n" + "\n".join(lines))
            elif _report_path(r):
                parts.append(f"Run {r.run_id} ({r.task_name}) has a report.")
        out.append({"campaign_id": cid, "name": camp.name, "objective": camp.objective, "text": "\n".join(parts),
                    "papers": titles})
    return out


def _access_for(user: Any) -> Any:
    from aurora.api.routes.brainstorm import _access
    from aurora.auth.guard import CURRENT
    token = CURRENT.set(user)
    try:
        return _access()
    finally:
        CURRENT.reset(token)


# ---------------------------------------------------------------------------- refresh

def is_old(it: dict[str, Any]) -> bool:
    """Published long before it was first seen (an old post of someone just followed)."""
    when = it.get("published_at") or 0
    return bool(when) and when < (it.get("fetched_at") or time.time()) - OLD_DAYS * 86400


def same_author(a: str, b: str) -> bool:
    """The same family name and the same first initial ("Maya Okafor", "M. Okafor"; not "Chidi Okafor")."""
    if not a or not b or family_name(a) != family_name(b):
        return False
    def given(name: str) -> str:
        return fold(name.split(",", 1)[1] if "," in name else name)[:1]
    return given(a) == given(b) != ""


def _mark_people(item: dict[str, Any], people: list[dict[str, Any]]) -> dict[str, Any]:
    """Tie an item to the followed people among its authors."""
    names = [n for n, _ in item.get("people") or []]
    pids = set(item.get("person_ids") or [])
    marked = []
    for n, pid in item.get("people") or []:
        match = next((p for p in people if same_author(p["name"], n)), None) if not pid else None
        if match and (item["kind"] != "paper" or match["wants"].get("papers", True)):
            pid = match["person_id"]
            pids.add(pid)
        marked.append([n, pid])
    item["people"] = marked
    item["person_ids"] = sorted(pids)
    if item["kind"] == "paper" and pids and names:
        first = next((p for p in people if p["person_id"] in pids), None)
        if first and not item.get("extra"):
            item["extra"] = f"{first['name']} is an author"
    return item


async def _gather(user_id: str, src: Sources, cfg: dict[str, Any], derived: dict[str, list[str]],
                  people: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, str]]:
    """Ask every source; (items, counts per source, problems per source)."""
    jobs: list[tuple[str, Any, dict[str, Any]]] = []      # (source key, coroutine, what to tie the items to)
    cats = list(dict.fromkeys([*cfg["topics"], *derived.get("categories", [])]))
    kws = list(dict.fromkeys([*cfg["keywords"], *derived.get("keywords", [])]))[:10]
    if cats or kws:
        jobs.append(("arxiv", src.arxiv_recent(cats, kws), {}))
    if kws:
        jobs.append(("openalex", src.openalex_recent(kws), {}))
    if cfg["hf"]:
        jobs.append(("hf", src.hf_daily(), {}))
    xk = x_key(user_id)
    for p in people:
        w = p["wants"]
        for ident in p["ids"]:
            if not ident.get("follow"):
                continue
            k, h = ident["kind"], ident.get("handle", "")
            tie = {"person": p}
            if k == "openalex" and w.get("papers", True):
                jobs.append(("openalex", src.openalex_author(h), tie))
            elif k == "s2" and w.get("papers", True):
                jobs.append(("s2", src.s2_author(h), tie))
            elif k == "bluesky" and w.get("posts", True):
                jobs.append(("bluesky", src.bluesky_author(h), tie))
            elif k == "mastodon" and w.get("posts", True):
                jobs.append(("mastodon", src.mastodon_account(h), tie))
            elif k == "x" and w.get("posts", True) and xk:
                jobs.append(("x", src.x_user(h, xk), tie))
            elif k == "arxiv" and w.get("papers", True) and h:
                jobs.append(("arxiv", src.arxiv_author(h), tie))
            elif k == "github" and w.get("code", True) and h:
                jobs.append(("github", src.github_repos(h), tie))
            elif k == "youtube" and w.get("videos", True) and h:
                jobs.append(("youtube", src.youtube(h), tie))
            elif k == "blog" and w.get("blog", True) and ident.get("url"):
                jobs.append(("rss", src.rss(ident["url"]), {**tie, "rss": None}))
    for i, feed in enumerate(cfg["rss"]):
        jobs.append(("rss", src.rss(feed["url"]), {"rss": i}))
    results = await asyncio.gather(*(j[1] for j in jobs), return_exceptions=True)
    items: list[dict[str, Any]] = []
    counts: dict[str, Any] = {}
    problems: dict[str, str] = {}
    for (key, _, tie), res in zip(jobs, results, strict=True):
        if isinstance(res, BaseException):
            msg = str(res) if isinstance(res, SourceError) else f"{type(res).__name__}"
            if not isinstance(res, SourceError):
                logger.warning("feed: %s failed", key, exc_info=res)
            problems.setdefault(key, msg)
            if tie.get("rss") is not None:
                cfg["rss"][tie["rss"]].update(state="error", error=msg)
            continue
        if key in ("rss", "youtube"):
            title, res = res
            if tie.get("rss") is not None:
                cfg["rss"][tie["rss"]].update(state="ok", error="", last_ok=time.time(),
                                             title=cfg["rss"][tie["rss"]].get("title") or title)
        counts[key] = counts.get(key, 0) + len(res)
        person = tie.get("person")
        for it in res:
            if person is not None:
                it["person_ids"] = sorted({*(it.get("person_ids") or []), person["person_id"]})
                if it["kind"] != "paper":
                    it["people"] = [[person["name"], person["person_id"]]]
            items.append(it)
    if "x" not in counts and not xk:
        counts["x"] = "off"
    return items, counts, problems


async def _discussions(user: Any, src: Sources, profiles: list[dict[str, Any]], new_papers: list[dict[str, Any]],
                       access: Any) -> list[dict[str, Any]]:
    """What people say about the papers in the projects, and about followed people's new papers."""
    from aurora.auth.store import auth_store
    from aurora.literature.library import library
    done: dict[str, float] = auth_store.get_pref(user.user_id, "feed_disc_done") or {}
    now = time.time()
    done = {k: v for k, v in done.items() if now - v < 7 * 86400}
    wanted: list[tuple[Paper, str, str]] = []      # (paper, its project, the followed person it is by)
    for p in profiles:
        for it in library.items(p["campaign_id"])[-8:]:
            entry = library.paper(it["paper_id"])
            if entry and (entry["paper"].arxiv_id or entry["paper"].doi):
                wanted.append((entry["paper"], p["campaign_id"], ""))
    for it in new_papers:
        if it.get("person_ids") and it.get("paper"):
            wanted.append((Paper.from_dict(it["paper"]), "", it["person_ids"][0]))
    out = []
    for paper, cid, about in wanted:
        key = paper.arxiv_id or paper.doi
        if not key or key in done or len(out) >= DISCUSSIONS_PER_REFRESH:
            continue
        done[key] = now
        link = f"arxiv.org/abs/{paper.arxiv_id}" if paper.arxiv_id else f"doi.org/{paper.doi}"
        posts, threads, forum = await asyncio.gather(src.bluesky_search(link), src.hn_threads(key),
                                                     src.openreview_forum(paper.title), return_exceptions=True)
        posts = [] if isinstance(posts, BaseException) else posts
        threads = [] if isinstance(threads, BaseException) else threads
        forum = "" if isinstance(forum, BaseException) else forum
        replies = sum(1 + p["replies"] for p in posts)
        comments = sum(t["comments"] for t in threads)
        if replies + comments < 4:
            continue
        places: list[list[str]] = []
        labels: list[list[str]] = []
        if posts:
            top = max(posts, key=lambda p: p["replies"] + p["likes"])
            places.append([f"{replies} posts and replies on Bluesky", f"https://bsky.app/search?q={link}"])
            labels.append([f"{replies} posts and replies on Bluesky", ""])
            places.append(["the most discussed post", top["url"]])
        for t in threads:
            places.append([f"a Hacker News thread ({t['comments']} comments)", t["url"]])
            labels.append([f"a Hacker News thread ({t['comments']} comments)", ""])
        if forum:
            places.append(["an OpenReview thread", forum])
            labels.append(["an OpenReview thread", ""])
        texts = [p["text"] for p in sorted(posts, key=lambda p: -(p["replies"] + p["likes"]))]
        texts += [c for t in threads for c in t["texts"]]
        summary, points = await rk.summarize_discussion(paper.title, texts, len(labels), access)
        ident = f"arXiv:{paper.arxiv_id}" if paper.arxiv_id else f"DOI {paper.doi}"
        out.append({"ukey": f"disc:{key}:{datetime.now().strftime('%G-%V')}", "kind": "discussion", "src": "discussion",
                    "source": f"Around {ident}", "url": places[0][1] if places else "", "title":
                    f"What people are saying about “{clip(paper.title, 110)}”", "summary": summary, "points": points,
                    "places": places, "people": labels, "about": about, "extra": f"gathered from {len(labels)} places",
                    "published_at": now, "for_project": cid})
    auth_store.set_pref(user.user_id, "feed_disc_done", done)
    return out


def _calls_left(user_id: str, spend: int = 0) -> int:
    from aurora.auth.store import auth_store
    day = datetime.now().strftime("%Y-%m-%d")
    used = auth_store.get_pref(user_id, "feed_calls") or {}
    n = used.get("n", 0) if used.get("day") == day else 0
    if spend:
        auth_store.set_pref(user_id, "feed_calls", {"day": day, "n": n + spend})
    return RANK_CALLS_PER_DAY - n - spend


async def rank_new(user: Any, profiles: list[dict[str, Any]], access: Any, forced: dict[str, str] | None = None) -> int:
    """Rank the items not ranked yet; returns how many."""
    items = feed_store.unranked(user.user_id)
    if not items:
        return 0
    for it in [x for x in items if is_old(x)]:          # not for the feed: no ranking needed
        feed_store.set_ranks(it["item_id"], [])
    items = [x for x in items if not is_old(x)]
    if not items:
        return 0
    if not profiles:
        for it in items:
            feed_store.set_ranks(it["item_id"], [])
        return len(items)
    dislikes = [d for d in feed_store.items(user.user_id, time.time() - 60 * 86400, hidden=True)][:30]
    lex = rk.Lexical(profiles, items, dislikes)
    scored = []
    for it in items:
        boost = 1.0 if it.get("person_ids") or it.get("about") else 0.0
        best = max(s for s, _ in lex.score(it)) + boost
        scored.append((best, boost, it))
    scored.sort(key=lambda x: -x[0])
    by_model: dict[str, list[tuple[str, int, str]]] = {}
    # a confidential project is described to the model only if its provider keeps no data
    from aurora.auth.providers import model_problem
    shared = [p for p in profiles if access is None or not model_problem(access.model, p["campaign_id"])]
    withheld = {p["campaign_id"] for p in profiles} - {p["campaign_id"] for p in shared}
    if access is not None and access.api_key and shared:
        top = [it for _, _, it in scored[: BATCH * 3]]
        for i in range(0, len(top), BATCH):
            if _calls_left(user.user_id) <= 0:
                break
            _calls_left(user.user_id, spend=1)
            by_model.update(await rk.model_ranks(top[i:i + BATCH], shared, access, dislikes))
    forced = forced or {}
    names = {p["campaign_id"]: p["name"] for p in profiles}
    for _, boost, it in scored:
        ranks = by_model.get(it["item_id"])
        if ranks is None:
            ranks = rk.lexical_ranks(lex, it, boost)
        elif withheld:
            ranks = sorted([*ranks, *(r for r in rk.lexical_ranks(lex, it, boost) if r[0] in withheld)],
                           key=lambda r: -r[1])[:2]
        cid = forced.get(it["item_id"])
        if cid and not any(r[0] == cid for r in ranks):      # a discussion of the project's own paper
            ranks = [(cid, 3, f"About a paper in {names.get(cid, 'this project')}’s literature."), *ranks][:2]
        feed_store.set_ranks(it["item_id"], ranks)
    return len(items)


async def refresh(user: Any, *, src: Sources | None = None) -> dict[str, Any]:
    """Ask every source, keep and rank what is new.  Returns what happened."""
    from aurora.auth.guard import CURRENT
    token = CURRENT.set(user)
    try:
        return await _refresh(user, src or sources())
    finally:
        CURRENT.reset(token)


async def _refresh(user: Any, src: Sources) -> dict[str, Any]:
    from aurora.auth.store import auth_store
    uid = user.user_id
    cfg = settings(uid)
    profiles = project_profiles(user)
    access = _access_for(user)
    derived: dict[str, list[str]] = {"keywords": [], "categories": []}
    if cfg["from_objectives"]:
        cache: dict[str, Any] = auth_store.get_pref(uid, "feed_topics") or {}
        for p in profiles:
            obj = p["objective"] or p["name"]
            key = rk.topics_key(obj)
            got = cache.get(p["campaign_id"])
            if not got or got.get("key") != key:
                from aurora.auth.providers import model_problem
                ok = access.api_key and not model_problem(access.model, p["campaign_id"])
                got = {"key": key, **await rk.objective_topics(obj, access if ok else None)}
                cache[p["campaign_id"]] = got
            derived["keywords"] += got.get("keywords", [])
            derived["categories"] += got.get("categories", [])
        auth_store.set_pref(uid, "feed_topics", cache)
    people = feed_store.people(uid)
    items, counts, problems = await _gather(uid, src, cfg, derived, people)
    save_settings(uid, {**(auth_store.get_pref(uid, "feed") or {}), "rss": cfg["rss"]})
    added = 0
    new_papers = []
    for it in items:
        _mark_people(it, people)
        row, new = feed_store.add_item(uid, it)
        if new:
            added += 1
            if row["kind"] == "paper":
                new_papers.append(row)
            if row["kind"] == "post" and it.get("links"):
                aid, doi = paper_ids_in(it["links"])
                if aid or doi:
                    feed_store.update_item(row["item_id"], extra=(row["extra"] + " · " if row["extra"] else "")
                                           + f"links to {'arXiv:' + aid if aid else 'DOI ' + doi}")
    forced: dict[str, str] = {}
    if cfg["discussions"]:
        try:
            for d in await _discussions(user, src, profiles, new_papers, access):
                cid = d.pop("for_project", "")
                row, new = feed_store.add_item(uid, d)
                if new:
                    added += 1
                    counts["discussion"] = counts.get("discussion", 0) + 1
                    if cid:
                        forced[row["item_id"]] = cid
        except Exception:
            logger.warning("feed: gathering discussions failed", exc_info=True)
    ranked = await rank_new(user, profiles, access, forced)
    feed_store.prune(uid, time.time() - KEEP_DAYS * 86400)
    day = datetime.now().strftime("%Y-%m-%d")
    old = status(uid)
    today = old.get("counts", {}) if old.get("date") == day else {}
    merged = {k: (today.get(k, 0) if isinstance(today.get(k), int) else 0) + v if isinstance(v, int) else v
              for k, v in counts.items()}
    merged = {**{k: v for k, v in today.items() if k not in merged}, **merged}
    _set_status(uid, date=day, counts=merged, problems=problems, last=time.time())
    return {"added": added, "ranked": ranked, "counts": counts, "problems": problems}


def start_refresh(user: Any) -> bool:
    """Refresh in the background; False if one is already running."""
    if running(user.user_id):
        return False

    async def go() -> None:
        try:
            await refresh(user)
        except Exception:
            logger.warning("feed refresh failed", exc_info=True)

    _running[user.user_id] = asyncio.create_task(go())
    return True


# ---------------------------------------------------------------------------- the view

def visible_rank(ranks: list[dict[str, Any]], projects: dict[str, str]) -> list[dict[str, Any]]:
    return [{**r, "project": projects[r["campaign_id"]]} for r in ranks if r["campaign_id"] in projects]


def activity(user_id: str, person: dict[str, Any]) -> tuple[str, int]:
    """("new paper · 2 posts", how many new) since their page was last opened."""
    rows = [r for r in feed_store.of_person(person["person_id"])
            if r["fetched_at"] > max(person["seen_at"], time.time() - 7 * 86400) and not is_old(r)]
    count: dict[str, int] = {}
    for r in rows:
        k = "about" if r["about"] == person["person_id"] else r["kind"]
        count[k] = count.get(k, 0) + 1
    words = {"paper": ("new paper", "new papers"), "post": ("post", "posts"), "blog": ("blog post", "blog posts"),
             "code": ("new repository", "new repositories"), "video": ("video", "videos"),
             "about": ("discussion of their work", "discussions of their work"), "discussion": ("discussion", "discussions")}
    parts = [(f"{n} " if n > 1 else "") + words[k][n > 1] for k, n in count.items() if k in words]
    return " · ".join(parts) or "nothing new this week", len(rows)


# ---------------------------------------------------------------------------- the digest

def digest_text(user: Any) -> tuple[str, str] | None:
    """(title, text) of today's digest; None when nothing fits well enough (and skipping is on)."""
    cfg = settings(user.user_id)["digest"]
    profiles = {p["campaign_id"]: p["name"] for p in project_profiles(user)}
    rows = feed_store.items(user.user_id, time.time() - 86400)
    ranks = feed_store.ranks([r["item_id"] for r in rows])
    per: dict[str, list[tuple[int, dict[str, Any], str]]] = {}
    for r in rows:
        for rr in ranks.get(r["item_id"], []):
            if rr["campaign_id"] in profiles and rr["rel"] >= 3:
                per.setdefault(rr["campaign_id"], []).append((rr["rel"], r, rr["why"]))
    try:                          # what came into the person's memory goes along
        from aurora.memory.relate import digest_text as memory_part
        memory = memory_part(user.user_id)
    except Exception:
        memory = ""
    if cfg.get("skip", True) and not any(rel >= 4 for v in per.values() for rel, _, _ in v) and not memory:
        return None
    if not per and not memory:
        return None
    lines = []
    for cid, picks in per.items():
        picks.sort(key=lambda x: -x[0])
        lines.append(f"## {profiles[cid]}")
        for _, r, why in picks[: int(cfg.get("top") or 5)]:
            lines.append(f"- {r['title']} ({r['source']})\n  {why}\n  {r['url']}")
        lines.append("")
    if memory:
        lines.append(memory)
    return f"Today’s reading, {datetime.now().strftime('%a %d %b')}", "\n".join(lines).strip()


async def send_digest(user: Any) -> str:
    """Send today's digest to the chosen channel; returns '' or why it was not sent."""
    from aurora.api.routes.campaigns import live_notifications
    from aurora.notify import deliver
    cfg = settings(user.user_id)["digest"]
    if cfg.get("to", "console") == "console":
        return "The digest goes only to the console"
    channel = next((c for c in live_notifications(user.user_id).channels if c.id == cfg["to"] or c.name == cfg["to"]), None)
    if channel is None:
        return "The chosen channel no longer exists"
    note = digest_text(user)
    if note is None:
        return "Nothing fits a project well today"
    return await deliver(channel, "daily_digest", note[0], note[1])


def _due(user_id: str, now: datetime) -> bool:
    from aurora.auth.store import auth_store
    cfg = settings(user_id)["digest"]
    if cfg.get("to", "console") == "console":
        return False
    try:
        h, m = (int(x) for x in str(cfg.get("at", "08:30")).split(":"))
    except ValueError:
        return False
    if (now.hour, now.minute) < (h, m):
        return False
    return auth_store.get_pref(user_id, "feed_digest_sent") != now.strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------- the loop

def _users() -> list[Any]:
    from aurora.auth.permissions import LOCAL_USER
    from aurora.auth.store import auth_store
    rows = auth_store.list_users()
    if not rows:
        return [LOCAL_USER]
    return [auth_store.as_user(r) for r in rows]


def _has_memory(user_id: str) -> bool:
    try:
        from aurora.memory.store import mem_store
        return bool(mem_store.sources(user_id, 1))
    except Exception:
        return False


async def tick() -> None:
    from aurora.auth.store import auth_store
    now = datetime.now()
    for user in _users():
        uid = user.user_id
        used = bool(auth_store.get_pref(uid, "feed_used"))
        if not used and not _has_memory(uid):
            continue
        last = float(status(uid).get("last") or 0)
        if used and time.time() - last > EVERY_HOURS * 3600 and not running(uid):
            try:
                await refresh(user)
            except Exception:
                logger.warning("feed refresh for %s failed", uid, exc_info=True)
        if _due(uid, now):
            auth_store.set_pref(uid, "feed_digest_sent", now.strftime("%Y-%m-%d"))
            error = await send_digest(user)
            if error:
                logger.info("feed digest for %s not sent: %s", uid, error)


async def loop() -> None:
    """Started with the web console."""
    while True:
        await asyncio.sleep(300)
        try:
            await tick()
        except Exception:
            logger.warning("feed loop failed", exc_info=True)
