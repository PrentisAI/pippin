"""Who leads a brainstorm, and how much the agents question the researcher on their own.

**Who leads** is a setting of each conversation, changed at any time (a switch above the message box, or in words:
"I'll lead" / "我来", "you lead" / "你来带"):

* **me** (the default): the researcher drives. The agents answer what they are asked, the conductor brings one in
  when the conversation calls for it, and *friction* (below) questions the researcher when they seem too sure.
* **pippin**: the agents drive. After each message a lead agent says where things stand, proposes the next step,
  writes drafts of it (to edit, take, or send back for another round) and ends with a concrete choice. The
  researcher steers by replying, by what they take and change, or by taking the lead back.

**Friction** (when the researcher leads): the agents challenge the researcher unasked, more or less by what the
conversation shows, decided by rules, without a model call:

* more when they assert something sweeping or certain, settle fast on one idea, take the AI's drafts with little
  change, or go a long while without anyone questioning them;
* less (or none) when they seem tired, are still exploring (the first messages), have just waved a challenge off
  ("not now") or let the last one pass, or set the agents to answer only when asked.

At most one challenge a turn, by the Skeptic, which says in a line before why it speaks. Each decision is kept as a
move (``friction``) for evaluation, fired or not, with the signals and the score.
"""

from __future__ import annotations

import re
from typing import Any

from aurora.brainstorm.store import bs_store

LEADS = ("me", "pippin")
LEAD_AGENT = "Methods"           # writes the drafts when the agents lead
THRESHOLD = {"moderate": 3, "high": 2}
QUIET_AFTER_WAVE = 6             # the researcher's messages without a challenge after "not now"
COOLDOWN = 3                     # ... and after any challenge
LONG_WITHOUT = 6                 # their messages without anyone questioning them that count as "a long while"

_SURE = re.compile(
    r"\b(obviously|clearly|definitely|certainly|surely|undoubtedly|of course|always|never|everyone|nobody|no one|"
    r"guarantee[sd]?|must be|will (?:definitely|certainly|surely)|there is no way|without (?:a )?doubt|proves?|"
    r"the only|best|solves?)\b|显然|肯定|一定|绝对|毫无疑问|必然|总是|从来不|从不|所有人|没有人|唯一|最好|必定|当然",
    re.I)

_ME = r"(?:i'?ll lead|i will lead|let me lead|i lead|i'?ll take (?:it|the lead) from here|我来带|我来主导|我主导|我来吧|我来)"
_YOU = r"(?:you lead|you take the lead|you take it from here|pippin,? lead|lead the way|你来带|你来主导|你主导|你带吧|你来吧|你来)"
_TAIL = r"(?:\s*(?:吧|了|好了|now|please))?"
_SEP = r"[\s,，。.!！:：;；]*"


def switch_word(text: str) -> tuple[str | None, str]:
    """Whether a message hands over the lead: (``me`` | ``pippin`` | None, the rest of the message).

    Only at the start of a message, so "我来说一下" is not taken for "我来"."""
    t = text.strip()
    for lead, words in (("me", _ME), ("pippin", _YOU)):
        m = re.match(rf"^{words}{_TAIL}(?:{_SEP}$|[\s,，。.!！:：;；]+)", t, re.I)
        if m:
            return lead, t[m.end():].strip()
    return None, t


def set_lead(chat_id: str, lead: str, *, by_words: bool = False) -> bool:
    """Change who leads; a system line says so. Whether it changed."""
    ch = bs_store.chat(chat_id)
    if ch is None or lead not in LEADS or ch["lead"] == lead:
        return False
    bs_store.update_chat(chat_id, lead=lead)
    if lead == "pippin":
        g = ch.get("guide") or {}
        if g.get("on"):              # the Mentor's guiding is the researcher leading: paused, kept
            bs_store.set_guide(chat_id, {**g, "on": False})
        line = ("Pippin leads now: after each of your messages the agents say where things stand, propose the next step "
                "with drafts, and ask you to choose. Reply, change a draft, or say “I'll lead” to take it back.")
    else:
        line = ("You lead now: the agents answer when you ask, join when the conversation calls for it, and question "
                "you when you seem very sure. Say “you lead” to hand it over again.")
    bs_store.add_message(chat_id, "system", line, kind="system")
    bs_store.add_move(chat_id, ch["space"], "lead", {"lead": lead, "by": "words" if by_words else "switch"})
    return True


LEAD_ASK = """(Console: you lead this conversation now; the researcher follows and steers. {what}
Do three things, briefly:
1. Where things stand, in one or two sentences (what is settled, what is open).
2. The next step you propose, and why it is the most useful one now.
3. Call draft_versions with two or three drafts of that step or of the idea as it should now read (differing in \
substance, each self-contained), then end your answer with one concrete choice for the researcher: which draft, \
or what to change.
Speak in the researcher's language. Do not ask open questions; offer the choice.)"""


def lead_ask(text: str) -> str:
    what = f"They just said: “{text[:1500]}”." if text.strip() else "They just handed you the lead."
    return LEAD_ASK.format(what=what)


# ---------------------------------------------------------------------------- friction

def _mine(history: list[dict[str, Any]], owner: str) -> list[int]:
    return [i for i, m in enumerate(history) if m["who"] == owner and m["kind"] == "text" and m["text"]]


def signals(history: list[dict[str, Any]], owner: str, chat: dict[str, Any]) -> dict[str, int]:
    """What calls for more friction (positive) or less (negative), by name."""
    out: dict[str, int] = {}
    mine = _mine(history, owner)
    if not mine:
        return out
    last = history[mine[-1]]["text"]
    if _SURE.search(last):
        out["sure"] = 2
    # settling fast: one idea in focus, talked about for a while, nothing else on the table from this conversation
    ideas = [c for c in bs_store.cards(chat["space"]) if c["type"] == "idea" and c.get("chat_id") == chat["chat_id"]]
    if chat.get("focus") and len(ideas) <= 1 and len(mine) >= 4:
        out["one_idea"] = 1
    # drafts taken with little change
    taken = [mv["data"] for mv in bs_store.moves(chat_id=chat["chat_id"]) if mv["kind"] == "taken"][-3:]
    if any(float(t.get("share", 1)) < 0.2 and not t.get("yours") for t in taken):
        out["took_drafts"] = 2
    # nobody has questioned them for a while
    challenged = [i for i, m in enumerate(history) if m["who"] == "Skeptic" or (m.get("widget") or {}).get("type") == "friction"]
    since = len([i for i in mine if not challenged or i > challenged[-1]])
    if since >= LONG_WITHOUT:
        out["unchallenged"] = 1
    if len(mine) <= 2:
        out["exploring"] = -2
    return out


def decide(history: list[dict[str, Any]], chat: dict[str, Any], owner: str, activity: str,
           answered: list[str]) -> dict[str, Any] | None:
    """Whether the Skeptic questions the researcher now, unasked: {"agent", "why", "score", "signals"}; the
    decision is kept as a move either way (except when it is not the researcher's turn at all)."""
    from aurora.brainstorm.conductor import tired
    if chat.get("lead", "me") != "me" or chat.get("run_id"):
        return None
    mine = _mine(history, owner)
    if not mine:
        return None
    f = chat.get("friction") or {}
    sig = signals(history, owner, chat)
    score = sum(sig.values())
    threshold = THRESHOLD.get(activity)
    held = ""
    if threshold is None:
        held = "agents only when asked"
    elif tired(history, owner):
        held = "tired"
    elif "Skeptic" in answered:
        held = "the Skeptic just answered"
    elif len(mine) < int(f.get("quiet_until", 0)):
        held = "waved off"
    elif f.get("last") is not None and len(mine) - int(f["last"]) < COOLDOWN:
        held = "a challenge just now"
    fire = not held and score >= (threshold or 99)
    bs_store.add_move(chat["chat_id"], chat["space"], "friction",
                      {"signals": sig, "score": score, "threshold": threshold, "fired": fire, "held": held,
                       "turn": len(mine)})
    if not fire:
        return None
    bs_store.set_friction(chat["chat_id"], {**f, "last": len(mine)})
    return {"agent": "Skeptic", "why": _why(sig, history[mine[-1]]["text"]), "score": score, "signals": sig}


def _why(sig: dict[str, int], text: str) -> str:
    zh = len(re.findall(r"[一-鿿]", text)) > len(text) / 4
    parts = {"sure": ("你说得很确定", "you sound very sure"),
             "one_idea": ("我们一直只围绕一个想法", "we have stayed with one idea for a while"),
             "took_drafts": ("你几乎原样采用了 AI 的草稿", "you took the AI's draft almost unchanged"),
             "unchallenged": ("这段时间还没人质疑过", "nobody has questioned this for a while")}
    said = [parts[k][0 if zh else 1] for k in parts if sig.get(k, 0) > 0]
    if zh:
        return "，".join(said) + "。值得先停一下。"
    return (", and ".join(said) + ": worth a pause.").capitalize() if said else "Worth a pause."


FRICTION_ASK = ("(You question the researcher on your own, unasked, because: {why}) Name the one assumption in what "
                "they just said that most needs checking, and ask it as one sharp question. Say what would convince "
                "you. Two or three sentences; no lists; do not repeat what was said.")


def wave(chat_id: str, msg_id: str) -> None:
    """The researcher waved a challenge off ("not now"): no challenge for a while."""
    ch = bs_store.chat(chat_id)
    if ch is None:
        return
    mine = len(_mine(bs_store.context(chat_id), ch["owner"]))
    bs_store.set_friction(chat_id, {**(ch.get("friction") or {}), "quiet_until": mine + QUIET_AFTER_WAVE})
    bs_store.add_move(chat_id, ch["space"], "friction_waved", {"msg_id": msg_id, "turn": mine})


# ---------------------------------------------------------------------------- tangents

RETURN_PROMPT = """A researcher went off on a tangent from a brainstorm and is now going back to the main conversation.
The main conversation was at: “{at}”
The tangent:
{talk}

Write, in the researcher's language and in their voice (first person), two to four sentences to post back in the main \
conversation: what the tangent was about and what it found or settled that matters there. Only what is in the \
tangent; no preamble."""


def tangent_talk(chat_id: str, owner: str) -> str:
    rows = [m for m in bs_store.messages(chat_id) if m["kind"] == "text" and m["text"] and not m["pending"]]
    return "\n".join(f"{'Researcher' if m['who'] == owner else m['who']}: {' '.join(m['text'].split())[:700]}"
                     for m in rows[-24:])
