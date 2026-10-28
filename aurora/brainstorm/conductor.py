"""The conductor: after the researcher speaks and the one they spoke to has answered, whether another agent should
join now, unasked. A brainstorm is not a sequence of stages the researcher steps through by buttons: a confident
claim brings the Skeptic in, a paper or a "has anyone done this" brings the Librarian, a direction that has
formed brings Methods, a question about a whole field brings a proposal to map it, an idea that holds up brings a
proposal for the round table.

Rules come first and keep it quiet: nothing when the person turned the agents' initiative off or seems tired, one
agent at most per turn, never one that spoke in the last few turns, and the costly moves (a map of the field, the
round table) are only proposed. Then one short call to the model decides between joining and (most often) not.
Whoever joins says why, in a line before its words.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from aurora.brainstorm import agents as ag
from aurora.brainstorm.store import bs_store

logger = logging.getLogger(__name__)

# off: no agent joins unasked and nothing is worked on quietly (tests of other things turn it off)
ENABLED = True
JOINERS = ("Skeptic", "Librarian", "Methods")
MOVES = (*JOINERS, "map", "roundtable", "none")
COOLDOWN = {"moderate": 3, "high": 1}        # the researcher's messages since an agent last joined on its own


def _said(history: list[dict[str, Any]], owner: str) -> list[dict[str, Any]]:
    return [m for m in history if m["who"] == owner and m["kind"] == "text" and m["text"]]


def tired(history: list[dict[str, Any]], owner: str) -> bool:
    """Their last two answers were short or unsure: not the moment for another voice."""
    from aurora.brainstorm.mentor import UNSURE
    last = [m["text"].strip() for m in _said(history, owner)[-2:]]

    def thin(t: str) -> bool:
        cjk = len(re.findall(r"[一-鿿]", t))
        return bool(UNSURE.search(t)) or (len(t) < 15 if cjk > len(t) / 3 else len(t.split()) < 7)
    return len(last) == 2 and all(thin(t) for t in last)


def may_join(history: list[dict[str, Any]], owner: str, activity: str, answered: list[str]) -> list[str]:
    """The agents that may join now, by the rules (before any model is asked)."""
    if activity not in COOLDOWN or tired(history, owner):
        return []
    mine = [i for i, m in enumerate(history) if m["who"] == owner and m["kind"] == "text"]
    out = []
    for agent in JOINERS:
        if agent in answered:
            continue
        spoke = [i for i, m in enumerate(history) if m["who"] == agent]
        # an agent that has not spoken may join at once; one that has waits a few of the researcher's messages
        if not spoke or len([i for i in mine if i > spoke[-1]]) >= COOLDOWN[activity]:
            out.append(agent)
    return out


def _board_note(board: ag.Board) -> str:
    idea = next((c for c in board.cards if c["card_id"] == board.chat.get("focus")), None) if board.chat.get("focus") else None
    if idea is None:
        ideas = [c for c in board.cards if c["type"] == "idea" and c.get("chat_id") == board.chat["chat_id"]]
        idea = ideas[-1] if ideas else None
    if idea is None:
        return "No idea is on the board for this conversation yet."
    # links between cards of the space (bs_store.links keeps those with both ends in the set given)
    links = bs_store.links({c["card_id"] for c in bs_store.cards(idea["space"])})
    into = [lk for lk in links if lk["to_card"] == idea["card_id"]]
    evidence = sum(lk["rel"] in ("supports", "challenges") for lk in into)
    questions = [bs_store.card(lk["from_card"]) for lk in into if lk["rel"] == "asks about"]
    open_q = sum(1 for q in questions if q and not q["answer"])
    return (f"The idea in focus: “{idea['text'][:300]}”. Evidence on it: {evidence}. Open questions on it: {open_q} "
            f"(answered: {len(questions) - open_q}). A map of the field: {'yes' if board.map else 'no'}.")


PROMPT = """You decide whether one more agent should join a research brainstorm now, unasked. Most turns: nobody.

Who could join, and only when it is clearly called for:
- Skeptic: the researcher just asserted something with confidence, or their idea rests on an assumption nobody in \
the conversation has questioned yet.
- Librarian: the researcher named a paper, a benchmark, a dataset or a checkable fact, or asked whether something \
exists or has been done, and the last answer did not settle it from the literature; or their idea has no evidence.
- Methods: a direction has formed (an idea is on the board, or they described a study they could do) and nobody \
has said how it could be tested.
- map: they ask what a whole field has done or how far it has come, and there is no map of the field yet.
- roundtable: the idea has evidence and its open questions are answered: it may be ready to be developed by \
several models.
- none: anything else; also when the last answer already did what the agent would do.

What counts as already done: the Mentor only asks questions and never designs, so a Mentor's question or \
counterpoint covers what the Skeptic would say, but never what Methods would add (a concrete design: what to \
measure, the controls, the baseline, the data) nor what the Librarian would add (papers found and read on the \
point). When the researcher has just described a study they could run and nobody has said concretely how to \
measure it, that is Methods' turn, whatever the Mentor asked.
{also}
May join now: {allowed}{costly}.

The board: {board}

The conversation (latest last):
{talk}

Answer with JSON only: {{"move": one of {moves}, "why": "one short sentence to the researcher, in their language, \
saying what in their words calls for it", "about": "what the agent should do, one short sentence (under 30 words)"}}"""


async def decide(history: list[dict[str, Any]], board: ag.Board, owner: str, activity: str, access: ag.ModelAccess,
                 answered: list[str], client: Any = None) -> dict[str, str] | None:
    """The move to make now, if any: {"move", "why", "about"}."""
    allowed = may_join(history, owner, activity, answered)
    if activity not in COOLDOWN or not access.api_key or tired(history, owner):
        return None
    costly = [m for m in ("map", "roundtable") if not (m == "map" and board.map)]
    if not allowed and not costly:
        return None
    talk = "\n".join(f"{'Researcher' if m['who'] == owner else m['who']}: {' '.join(m['text'].split())[:500]}"
                     for m in history[-10:] if m["kind"] in ("text", "system") and m["text"])
    prompt = PROMPT.format(allowed=", ".join(allowed) or "no agent (only a proposal)",
                           costly=(", and these proposals: " + ", ".join(costly)) if costly else "",
                           also="" if activity == "high" else "Be sparing: join only when the reason is plain.",
                           board=_board_note(board), talk=talk, moves=list(MOVES))
    client = client or ag._client(access)
    try:
        resp = await client.chat.completions.create(model=access.model, temperature=0, max_tokens=900,
                                                    messages=[{"role": "user", "content": prompt}])
        raw = resp.choices[0].message.content or ""
        data = json.loads(re.search(r"\{.*\}", raw, re.S).group(0))  # type: ignore[union-attr]
    except Exception:
        logger.debug("the conductor could not decide", exc_info=True)
        return None
    move = str(data.get("move", "none"))
    if move not in MOVES or move == "none" or (move in JOINERS and move not in allowed) or (move not in JOINERS and move not in costly):
        return None
    return {"move": move, "why": " ".join(str(data.get("why", "")).split())[:300],
            "about": " ".join(str(data.get("about", "")).split())[:300]}


JOIN_ASK = ("(You join this conversation on your own, unasked, because: {why}) {about} Be brief: two to four "
            "sentences, at most one question, building on what was just said rather than repeating it.")
