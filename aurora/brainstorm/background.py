"""What the agents do on their own, by each person's setting ("How active").

* **Moderately**: a few suggestions a day per space you work in, when new material or an unanswered
  question calls for one ("What's new": a paper that bears on an idea, two ideas that could be one,
  a question that has waited).
* **Very**: the same, more often, and the agents keep working in the background: the Librarian looks for
  evidence on ideas that have none, the Skeptic takes a first look at ideas nobody has questioned. What they
  do lands in each idea's own discussion, and a line in "What's new" says so.

Everything runs with that person's API key, and is capped per day.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any

from aurora.brainstorm import agents as ag
from aurora.brainstorm.store import bs_store

logger = logging.getLogger(__name__)

TICK_MINUTES = 10
ACTIVE_DAYS = 7                      # only spaces the person used lately
EVERY = {"moderate": 8 * 3600, "high": 3600}          # how often a space is looked at
DAILY_CALLS = {"moderate": 4, "high": 40}            # model calls per person per day, at most
QUESTION_WAITS = 2 * 86400            # a question unanswered this long is brought up


def _day() -> str:
    return time.strftime("%Y-%m-%d")


def _budget(user_id: str, activity: str, spend: int = 0) -> bool:
    """Whether the person's daily allowance has room (and take ``spend`` from it)."""
    from aurora.auth.store import auth_store
    key = f"bs_calls:{_day()}"
    used = int(auth_store.get_pref(user_id, key, 0) or 0)
    if used + max(spend, 1) > DAILY_CALLS.get(activity, 0):
        return False
    if spend:
        auth_store.set_pref(user_id, key, used + spend)
    return True


def _waiting_questions(space: str, owner: str, cards: list[dict[str, Any]], links: list[dict[str, Any]]) -> int:
    """A question nobody has answered for days, that an idea waits on."""
    n = 0
    now = time.time()
    for q in cards:
        if q["type"] != "question" or q["answer"] or now - q["created_at"] < QUESTION_WAITS:
            continue
        if bs_store.recent_suggestion(space, owner, "open", q["card_id"]):
            continue
        days = int((now - q["created_at"]) // 86400)
        ideas = [lk["to_card"] for lk in links if lk["from_card"] == q["card_id"]]
        bs_store.add_suggestion(space, owner, "Skeptic", "open",
                                f"A question has waited {days} days for an answer: “{q['text'][:140]}”"
                                + (f" {len(ideas)} idea{'s' if len(ideas) > 1 else ''} depend on it." if ideas else ""),
                                "Answer it", cards=[q["card_id"]])
        n += 1
    return n


_SUGGEST = """\
You help a researcher keep a brainstorm moving. Below are their ideas, open questions and papers that recently \
joined the project's literature. Suggest at most 3 things worth their attention now, only where there is a real \
reason (otherwise fewer, or none):
- evidence: a recent paper bears on an idea (it supports or challenges it);
- caveat: something weakens the support of an idea;
- combine: two ideas could become one design or one model.
Reply with JSON only: {"suggestions": [{"by": "Librarian" | "Methods" | "Skeptic", "kind": "evidence" | \
"caveat" | "combine", "short": "one sentence for the researcher", "act": "2 to 4 words for the button", \
"cards": ["idea ids"], "paper_key": "for evidence or caveat", "rel": "supports" | "challenges"}]}"""


async def suggest(space: str, owner: str, cards: list[dict[str, Any]], links: list[dict[str, Any]], literature: Any,
                  access: ag.ModelAccess, client: Any = None) -> int:
    """Ask the model for a few suggestions; keep the ones that name real ideas and papers."""
    ideas = [c for c in cards if c["type"] == "idea"]
    if not ideas or not access.api_key:
        return 0
    on_board = {c["paper_id"] for c in cards if c["paper_id"]}
    recent = []
    if literature is not None:
        for e in literature.items():
            if e.get("added_at", 0) > time.time() - 3 * 86400 and e["paper_id"] not in on_board:
                recent.append(e)
    lines = ["Ideas:"] + [f"- {c['card_id']}: {c['text']}" for c in ideas[:20]]
    lines += ["Open questions:"] + [f"- {c['card_id']}: {c['text']}" for c in cards if c["type"] == "question" and not c["answer"]][:15]
    lines += ["Recent papers:"] + [f"- [{e['key']}] {e['paper'].title}: {' '.join(e['paper'].abstract.split()[:60])}"
                                   for e in recent[:12]]
    client = client or ag._client(access)
    try:
        resp = await client.chat.completions.create(model=access.model, temperature=0.2, max_tokens=1500,
                                                    messages=[{"role": "system", "content": _SUGGEST},
                                                              {"role": "user", "content": "\n".join(lines)}])
        raw = resp.choices[0].message.content or ""
        data = json.loads(re.search(r"\{.*\}", raw, re.S).group(0))      # type: ignore[union-attr]
    except Exception:
        logger.info("brainstorm suggestions for %s failed", space, exc_info=True)
        return 0
    from aurora.literature.library import library
    ids = {c["card_id"] for c in ideas}
    n = 0
    for sgg in (data.get("suggestions") or [])[:3]:
        kind = sgg.get("kind")
        picked = [c for c in sgg.get("cards") or [] if c in ids]
        if kind not in ("evidence", "caveat", "combine") or not picked or sgg.get("by") not in ag.AGENTS:
            continue
        paper_id = ""
        if kind in ("evidence", "caveat"):
            entry = library.paper(str(sgg.get("paper_key", "")).strip("[] "))
            if entry is None or entry["paper_id"] not in {e["paper_id"] for e in recent}:
                continue
            paper_id = entry["paper_id"]
            if bs_store.recent_suggestion(space, owner, kind, paper_id):
                continue
        elif len(picked) < 2 or bs_store.recent_suggestion(space, owner, kind, picked[0] + picked[1]):
            continue
        rel = "challenges" if kind == "caveat" or sgg.get("rel") == "challenges" else "supports"
        bs_store.add_suggestion(space, owner, sgg["by"], kind, str(sgg.get("short", ""))[:400],
                                str(sgg.get("act", "Show it"))[:40] or "Show it", cards=picked[:2],
                                paper_id=paper_id, rel=rel)
        n += 1
    return n


async def keep_working(space: str, owner: str, board_for: Any, access: ag.ModelAccess, limit: int = 2) -> int:
    """'Very': the Librarian looks for evidence on ideas without any; the Skeptic questions unquestioned ones."""
    done = 0
    cards = [c for c in bs_store.cards(space) if c["owner"] == owner or (c["vis"] == "project" and not space.startswith("user:"))]
    links = bs_store.links({c["card_id"] for c in cards})
    for idea in [c for c in cards if c["type"] == "idea"]:
        if done >= limit:
            break
        into = [lk for lk in links if lk["to_card"] == idea["card_id"]]
        has_evidence = any(lk["rel"] in ("supports", "challenges") for lk in into)
        has_question = any(lk["rel"] == "asks about" for lk in into)
        agent, ask = (("Librarian", "Look for evidence on this idea, for and against.") if not has_evidence else
                      ("Skeptic", "Take a first look at this idea: what is its weakest point?") if not has_question else
                      (None, ""))
        if agent is None or bs_store.recent_suggestion(space, owner, "worked", idea["card_id"] + agent):
            continue
        thread = {"chat_id": f"card:{idea['card_id']}", "space": space, "owner": idea["owner"],
                  "title": idea["text"][:60], "vis": idea["vis"], "run_id": "", "focus": idea["card_id"]}
        board = board_for(thread)
        await ag.answer(agent, board, bs_store.messages(thread["chat_id"]), ask, access, focus=idea["card_id"])
        bs_store.add_suggestion(space, owner, agent, "worked",
                                f"{agent} {'looked for evidence on' if agent == 'Librarian' else 'questioned'} "
                                f"“{idea['text'][:100]}” on its own.", "Show it", cards=[idea["card_id"]],
                                paper_id=idea["card_id"] + agent)
        done += 1
    return done


async def run_once(user_id: str, activity: str) -> None:
    """Look at the spaces one person worked in lately (in their name, with their key)."""
    from aurora.api.routes import brainstorm as routes
    from aurora.auth.guard import CURRENT
    from aurora.auth.permissions import LOCAL_USER
    from aurora.auth.store import auth_store
    if user_id == "local":
        user = LOCAL_USER
    else:
        row = auth_store.get_user(user_id)
        if row is None:
            return
        user = auth_store.as_user(row)
    token = CURRENT.set(user)
    try:
        for space in bs_store.spaces_of(user_id, time.time() - ACTIVE_DAYS * 86400):
            last_key = f"bs_bg_last:{space}"
            if time.time() - float(auth_store.get_pref(user_id, last_key, 0) or 0) < EVERY[activity]:
                continue
            try:
                _, cid = routes._space(space)
            except Exception:
                continue
            auth_store.set_pref(user_id, last_key, time.time())
            cards = [c for c in bs_store.cards(space) if routes._sees_card(c, user_id)]
            links = bs_store.links({c["card_id"] for c in cards})
            _waiting_questions(space, user_id, cards, links)
            access = routes._access()
            if not access.api_key or not _budget(user_id, activity):
                continue
            _budget(user_id, activity, spend=1)
            await suggest(space, user_id, cards, links, routes._literature(space, cid), access)
            if activity == "high" and _budget(user_id, activity, spend=2):
                await keep_working(space, user_id, lambda th, c=cid: routes._board(th, c), access)
    finally:
        CURRENT.reset(token)


async def loop() -> None:
    """Started with the web console."""
    while True:
        await asyncio.sleep(TICK_MINUTES * 60)
        try:
            from aurora.auth.store import auth_store
            users = [u["user_id"] for u in auth_store.list_users()] or ["local"]
            for uid in users:
                activity = auth_store.get_pref(uid, "brainstorm_activity", "moderate")
                if activity in EVERY:
                    await run_once(uid, activity)
        except Exception:
            logger.warning("brainstorm background work failed", exc_info=True)
