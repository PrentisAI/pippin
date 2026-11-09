"""The Mentor: guided questioning in a brainstorm, after the Socratic Mentor of Academic Research Skills (ARS).

The Mentor helps a researcher think a direction through by asking, not by proposing: their research question
stays theirs. It follows the ARS protocol, read at run time from ``aurora/third_party/ars`` (CC BY-NC 4.0,
unmodified): five layers of questions (the problem, the method, the evidence, their own critique, the
contribution), the researcher's predictions before evidence is shown, counterpoints when they sound certain,
one adjacent facet at a time, and the insights they reach, which become their research plan. Only when they
ask for it does it propose questions itself, and it says so first; those are marked as the AI's.

What the protocol has the model write inline (``[INSIGHT: ...]``, ``[Q:PROBE]``, the exit marker) is taken
out of the reply and kept in the conversation's *guide*; the researcher sees plain conversation. Pippin adds a
few markers of its own the same way (the layer, the intent, the convergence signals, a research question ready
to be written down, the end), listed in :data:`ADAPTER`.

When the research question converges, the ARS research question agent writes it down from the conversation (an
RQ Summary, or, when the researcher asked for candidates, AI-generated candidates with their FINER scores). The
researcher confirms it or says what to change, in the conversation. A confirmed question goes on the board as
theirs, and a map of its field starts from it without asking for the scope again.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from aurora.brainstorm.store import bs_store
from aurora.review.search import ars_section

logger = logging.getLogger(__name__)

SECTIONS = ("question", "method", "evidence", "limits", "contribution")
SECTION_NAMES = {"question": "Research question", "method": "Methodology direction", "evidence": "Evidence strategy",
                 "limits": "Known limitations", "contribution": "Expected contribution"}
LAYER_SECTION = dict(enumerate(SECTIONS, start=1))
CERTAIN = re.compile(r"\b(obviously|clearly|definitely|certainly|undeniably|without (a )?doubt|of course|surely)\b"
                     r"|显然|肯定|一定是|毫无疑问|毋庸置疑|明显|当然", re.I)
# an answer that says little: the researcher may be tired of being asked, or out of their depth
UNSURE = re.compile(r"\b(no idea|not sure|don'?t know|dunno|both|either|multiple reasons|whatever|i guess)\b"
                    r"|不知道|不确定|没想法|都可以|都行|随便|不清楚|说不好", re.I)
MENTOR_FILE = "socratic_mentor_agent.md"
RQ_FILE = "research_question_agent.md"


def default_guide() -> dict[str, Any]:
    return {"on": False, "layer": 1, "turns": 0, "layer_turns": 0, "intent": "", "predictions": True,
            "suggesting": False, "signals": [], "questions": [], "commitments": [], "probes": [], "counterpoints": {},
            "health": "", "ended": "", "rq": None, "skeptic": [], "pending": [],
            "scope": [], "predicted_at": -99, "recapped_at": 0, "direction_card": ""}


def guide_of(chat: dict[str, Any]) -> dict[str, Any]:
    return {**default_guide(), **(chat.get("guide") or {})}


def active(chat: dict[str, Any]) -> bool:
    """The Mentor is guiding this conversation: the researcher's next message is for it."""
    g = guide_of(chat)
    return bool(g["on"]) and not g["ended"]


# ---------------------------------------------------------------------------
# The protocol, as the model reads it

# (heading to start at, heading to stop before, a heading to look after): the reading probe is left out (it is
# dormant by default in ARS too), and so is the activation of the adjacent-framing probe (Pippin turns it on)
PROTOCOL_SECTIONS = [
    ("## Role Definition", "## Wording-Pattern Advisory", ""),
    ("## Wording-Pattern Advisory", "## Intent Detection Layer", ""),
    ("## Intent Detection Layer", "## SCR Protocol", ""),
    ("## SCR Protocol", "## Optional Reading Probe Layer", ""),
    ("## Optional Adjacent-Framing Probe Layer", "### Activation", ""),
    ("### Trigger Tendency", "## Dialogue Management Rules", "## Optional Adjacent-Framing Probe Layer"),
    ("## Dialogue Management Rules", "## Collaboration with Other Agents", ""),
    ("## Dialogue Health Indicator", None, ""),
]

ADAPTER = """\
HOW THIS RUNS IN Pippin (it overrides the protocol where they differ)
- You are the Mentor in a research brainstorm. Three other agents are in the room: the Skeptic (finds the weak \
points; it plays the protocol's devil's advocate, and the console brings it in when the researcher has settled \
their method and when they examine their conclusions), Methods (turns ideas into tests) and the Librarian \
(searches the literature, maps a field, reads papers closely). Whatever the protocol says about other ARS agents, \
modes, files, ledgers, passports, environment variables and Stage 6 does not apply here.
- The adjacent-framing layer is ON (in exploratory intent, Layer 1). The reading probe stays dormant.
- Never talk about "the system" or "the console": say "I'll write it down for you to check", not "the system will".
- Write as in a conversation: short plain prose, in the researcher's language and script: Simplified Chinese \
stays Simplified, never Traditional (technical terms may stay in English). Usually under 150 words, never over 400. No headings, no tables, no bold labels like "Layer 2", never \
the internal names (SCR, layer, probe, commitment gate, INSIGHT, convergence signal).
- When you bring in a counterpoint from the literature (a divergence reveal, a certainty-triggered contradiction), \
search first (search_papers) and name the one paper it comes from by its key in square brackets, e.g. [Behrens12]. \
Never cite from memory; if the search finds nothing fitting, give the counterpoint as a perspective without a \
citation. Otherwise do not list references.
- When what matters is in a paper's body, not its abstract (how its ground truth was made, what exactly it \
measured), read it (read_paper, a few pages at a time) instead of guessing or asking the researcher to paste it; \
say what you read in a sentence and go on asking. Never say you cannot open papers.
- The researcher may have a map of the field in this space (below). Its unsolved problems are good material for \
Layer 1: ask which of them bothers them most and why, never which is most promising.
- The tags the protocol asks for ([INSIGHT: ...], [COMMITMENT: ...], [Q:CLARIFY] and the other question types, \
[ADJACENT-PROBE: ...], [HEALTH-CHECK: ...], the exit marker) go on lines of their own after your reply. The console \
takes them out before the researcher sees it. An insight may name its section first: [INSIGHT: method | ...] \
(sections: question, method, evidence, limits, contribution; without one, the current layer's).
- Also, at the end of every reply, on lines of their own:
  [LAYER: n] the layer you are in after this reply (1 to 5);
  [INTENT: exploratory] or [INTENT: goal] once you can tell;
  [SIGNALS: S1, S4] the convergence signals active now (empty if none);
  [COUNTERPOINT] when this reply carries a certainty-triggered contradiction.
- [RQ-READY] when the researcher asks to pin their question down or agrees to your offer to write it down, or, in \
goal-oriented intent, when they can state it in one sentence (S1) and it has been stable (they decide when it is \
settled; you may offer, see CONVERSATION CRAFT). The console then writes it down from the conversation (an RQ Summary) for them to confirm, so do not \
write it out yourself. Once per question; again only if the question changed.
- [END: converged | user | stagnation | max] when the dialogue ends by the protocol's auto-end conditions, or the \
researcher asks to stop being guided. Your reply is then the closing summary, in prose. Their plan is kept beside \
the conversation.
- [PREDICTIONS: off] or [PREDICTIONS: on] when the researcher asks you to stop or start asking for their \
predictions first.
- After the exit marker you give AI-generated starting points (three to five research questions, labeled as \
yours, in prose), then [RQ-CANDIDATES] so the console writes them down with their FINER scores for the \
researcher to take one or not. [GUIDED: resume] when they ask you to go back to guiding them by questions.

CONVERSATION CRAFT (Pippin's own rules; they override the protocol where they differ, learned from long sessions in \
which researchers tired of being questioned)
- One question per reply. A second only as a short either/or of the same question. Never stack two or three \
questions: the researcher answers one and the rest are lost.
- Their predictions: ask for one at most every four of your replies (the state below says how long ago you last \
did), and mark the reply [PREDICT]. Never right after they made a decision or narrowed their scope, never when \
they asked you something factual, never after a short or unsure answer. A prediction is a tool, not a ritual.
- Questions of fact or orientation (which fields work on this, what a paper did, how a benchmark made its ground \
truth, whether something exists) are not their research question: answer them directly first, concretely, from a \
search or from reading the paper, naming the papers. Then ask your one question. Non-generation is about their \
research question and their ideas, never about withholding what is known.
- Their scope is binding. When they decide something about the frame (who or what is studied, what is in or out, \
whether people take part, what to do first), mark it [SCOPE: the decision, in a few words]. The decisions so far \
are listed below: every question, analogy and counterpoint stays inside them. Before an analogy from another \
setting, check it does not quietly change the frame (an analogy about people's memory is not a question about the \
agent's memory). When they say you drifted, take their frame and go back to it.
- When their last two answers were short or unsure (the state below says so), stop probing. In two or three \
sentences say where they have got to, in their own words, then offer two or three concrete ways on, phrased as \
choices (go deeper on X, look at what is known about Y, write the question down, or hear your view), and let them \
pick. Do not ask a new probing question in that reply. When they then pick ("the first", "both", "ok", in any \
words), do it: with "both", start with the first and say the other comes next. Never offer the same choices again.
- Converge. About every eight of your replies, or as soon as they narrow to a direction ("the part we can do now is \
X"), recap in two sentences and offer to write their question down as a first version they can change. When they \
agree, or ask for it, give [RQ-READY] (in any intent). A first version is a draft, not an end: the conversation goes \
on from it.
- When a direction of theirs has formed, mark it in that same reply: as soon as they describe a study they could \
actually do (what data, what is measured, even roughly), [DIRECTION: the direction, one or two sentences, close to \
their words]. Do not wait for it to be polished; it is a draft. It is put on their board as a draft idea \
of theirs, which the other agents can then work on; a later [DIRECTION] revises it.
- The researcher may at any time ask another agent, map the field, or have the round table work on their idea; \
the conversation is not a sequence of stages. Take up what those brought in (it is in the conversation) and go on \
from where they are now.

Pippin EXTENSIONS (beyond the ARS protocol; from Maier, Schneider and Feuerriegel, CHI 2026, where a question mode \
with these moves gave better ideas that stayed diverse and that people felt were their own)
- A stimulus. When the researcher is stuck on one version of their idea (exploring, Layer 1), you may give ONE of: \
a piece of specific knowledge they may not have (a capability, a result, a lesser-known fact), or an analogy from \
another field that solves a similar problem. Ground it in a paper you found with search_papers and cite its key; \
never from memory. One or two sentences, then only ask how it could inspire a new version of their idea. Never \
turn it into an idea yourself. Mark it [STIMULUS: knowledge] or [STIMULUS: analogy].
- Their versions. After a stimulus or two, ask them to write two or three different versions of their idea in their \
own words. When they have written several, [VERSIONS]: the console lists them exactly as written for them to pick \
one. Do not rewrite or rank them.
- Drafts. When they ask you to write versions for them ("write it for me", "你帮我写"), that is model-led work: emit \
the exit marker (it leaves non-generation mode visibly), then call draft_versions with two or three drafts. They edit \
or take one, or ask for another round from it; the console tells you what they did.
- When they took an AI draft without changing it, ask one question: what in it is most theirs, and what are they \
unsure of, so that it becomes theirs.
- End a move with one plain sentence offering the natural next step (a stimulus, writing versions, picking one, \
going deeper, letting the Skeptic attack it, mapping the field, developing it with the round table), and follow \
whatever they answer."""


def _section(file: str, start: str, stop: str | None, after: str) -> str:
    if not after:
        return ars_section(file, start, stop)
    from aurora.review.search import ARS
    text = (ARS / file).read_text(encoding="utf-8") if (ARS / file).is_file() else ""
    i = text.find(start, max(0, text.find(after)))
    if i < 0:
        return ""
    j = text.find(stop, i + len(start)) if stop else -1
    return text[i: j if j > 0 else len(text)].strip()


def protocol() -> str:
    parts = [_section(MENTOR_FILE, a, b, after) for a, b, after in PROTOCOL_SECTIONS]
    parts.append(ars_section(RQ_FILE, "#### FINER Guiding Questions", "### Collaboration with socratic_mentor_agent"))
    return "\n\n".join(p for p in parts if p)


def system_prompt(board: Any) -> str:
    return ("The protocol below is from Academic Research Skills (Cheng-I Wu, CC BY-NC 4.0). Follow it.\n\n"
            f"PROTOCOL\n{protocol()}\nEND OF PROTOCOL\n\n{ADAPTER}")


def state_note(chat: dict[str, Any], text: str) -> str:
    """Where the guided conversation stands, for the Mentor (never shown)."""
    g = guide_of(chat)
    ins = bs_store.insights(chat["chat_id"])
    lines = [f"Layer {g['layer']} (your replies in it so far: {g['layer_turns']}; in the whole dialogue: {g['turns']}).",
             f"Intent: {g['intent'] or 'not classified yet'}. Predictions first: {'on' if g['predictions'] else 'off (the researcher asked)'}."
             + (" You are outside non-generation mode at the researcher's request." if g["suggesting"] else ""),
             "Insights so far: " + (", ".join(f"{s} {sum(i['section'] == s for i in ins)}" for s in SECTIONS) if ins else "none")
             + (". Latest: " + "; ".join(i["text"] for i in ins[-4:]) if ins else ""),
             f"Convergence signals active: {', '.join(g['signals']) or 'none'}.",
             "Recent question types: " + (", ".join(g["questions"][-6:]) or "none") + "."]
    if g["scope"]:
        lines.append("The scope they set (binding: stay inside it): " + "; ".join(g["scope"]))
    since = g["turns"] - g["predicted_at"]
    lines.append(f"You last asked for a prediction {since} of your replies ago" if since < 50 else
                 "You have not asked for a prediction yet")
    said = [m["text"].strip() for m in bs_store.messages(chat["chat_id"])
            if m["kind"] == "text" and m["who"] == chat["owner"] and m["text"]][-2:]
    def thin(t: str) -> bool:
        cjk = len(re.findall(r"[\u4e00-\u9fff]", t))
        return bool(UNSURE.search(t)) or (len(t) < 15 if cjk > len(t) / 3 else len(t.split()) < 7)
    if len(said) == 2 and all(thin(t) for t in said):
        lines.append("Their last two answers were short or unsure: stop probing now (see the rules: recap, then offer "
                     "ways on as choices).")
    if g["turns"] - g["recapped_at"] >= 8 and not (g.get("rq") or {}).get("state") == "settled":
        lines.append(f"{g['turns'] - g['recapped_at']} of your replies since the last recap: recap and offer to write "
                     "their question down as a first version.")
    if g.get("direction_card") and (c := bs_store.card(g["direction_card"])):
        lines.append(f"Their direction on the board (a draft idea of theirs): {c['text']}")
    if g["commitments"]:
        lines.append("Their predictions so far: " + "; ".join(c["text"] for c in g["commitments"][-5:]))
    if g["probes"]:
        lines.append("Adjacent facets already surfaced: " + "; ".join(p for p in g["probes"]))
    used = g["counterpoints"].get(str(g["layer"]), 0)
    if CERTAIN.search(text or ""):
        lines.append(f"Their last message sounds certain (“{CERTAIN.search(text).group(0)}”). Certainty-triggered "  # type: ignore[union-attr]
                     f"contradictions used in this layer: {used} of 2.")
    rq = g.get("rq")
    if rq and rq.get("state") == "settled":
        lines.append(f"Their research question, confirmed by them: {rq.get('question', '')}")
    elif rq and rq.get("state") == "draft":
        lines.append("Their research question is written down and waits for their word.")
    if g["skeptic"]:
        lines.append("The Skeptic has challenged them as devil's advocate (on " + " and ".join(g["skeptic"]) + "); "
                     "its questions are in the conversation: build on them.")
    return "WHERE THE CONVERSATION STANDS (internal; never say it)\n" + "\n".join(f"- {x}" for x in lines)


# ---------------------------------------------------------------------------
# After a reply: the tags out, the guide and the plan updated

_TAGS = ("INSIGHT", "COMMITMENT", "Q", "ADJACENT-PROBE", "HEALTH-CHECK", "SOCRATIC-NON-GENERATION-EXIT",
         "WORDING_PATTERN_ADVISORY", "READING-PROBE", "LAYER", "INTENT", "SIGNALS", "COUNTERPOINT", "RQ-READY",
         "RQ-CANDIDATES", "END", "PREDICTIONS", "GUIDED", "STIMULUS", "VERSIONS", "SCOPE", "PREDICT", "DIRECTION")
_TAG = re.compile(r"\[(" + "|".join(re.escape(t) for t in sorted(_TAGS, key=len, reverse=True)) + r")\b:?\s*([^\]\n]*)\]")
EXIT = "Leaving guided questioning at your request: what follows are AI-generated starting points"


def take_tags(text: str) -> tuple[str, list[tuple[str, str]]]:
    """The reply without its tags, and the tags (name, body) in order."""
    tags = [(m.group(1), m.group(2).strip()) for m in _TAG.finditer(text)]
    clean = _TAG.sub("", text)
    clean = re.sub(r"[ \t]+\n", "\n", clean)
    clean = re.sub(r"\n{3,}", "\n\n", clean).strip()
    return clean, tags


def after_turn(chat_id: str, viewer: str, text: str, *, console: bool = False) -> tuple[str, list[list[str]]]:
    """Apply a Mentor reply to the guide and the plan; returns what the researcher sees and the effects to show.

    ``console``: the turn was the console's (a confirmed question, a finished map), not an answer to the
    researcher's words, so it notes nothing as theirs."""
    chat = bs_store.chat(chat_id)
    clean, tags = take_tags(text)
    if chat is None:                # a card's thread: no guided dialogue there, only its words
        return clean, []
    g = guide_of(chat)
    effects: list[list[str]] = []
    last_user = next((m for m in reversed(bs_store.messages(chat_id)) if m["who"] == viewer and m["kind"] == "text"), None)
    before = g["layer"]
    g["on"] = True
    g["turns"] += 1
    g["layer_turns"] += 1
    noted = 0
    for name, body in tags:
        if name == "INSIGHT" and body and not g["suggesting"] and not console:
            section, _, rest = body.partition("|")
            section = section.strip().lower()
            if rest and section in SECTIONS:
                body = rest.strip()
            else:
                section = LAYER_SECTION.get(g["layer"], "question")
            bs_store.add_insight(chat_id, chat["space"], viewer, section, body,
                                 quote=(last_user or {}).get("text", "")[:600], msg_id=(last_user or {}).get("msg_id", ""))
            noted += 1
        elif name == "COMMITMENT" and body:
            g["commitments"].append({"text": body[:400], "layer": g["layer"]})
        elif name == "Q" and body:
            g["questions"] = [*g["questions"], body.upper()][-12:]
        elif name == "ADJACENT-PROBE":
            facet = re.search(r'surfaced="([^"]+)"', body)
            g["probes"].append(facet.group(1) if facet else body[:100])
        elif name == "HEALTH-CHECK":
            g["health"] = body[:300]
        elif name == "SOCRATIC-NON-GENERATION-EXIT":
            g["suggesting"] = True
            effects.append([EXIT, ""])
        elif name == "GUIDED" and body.lower().startswith("resume"):
            g["suggesting"] = False
            effects.append(["Back to guiding by questions", ""])
        elif name == "LAYER":
            n = re.search(r"[1-5]", body)
            if n:
                g["layer"] = int(n.group(0))
        elif name == "INTENT":
            g["intent"] = "goal" if "goal" in body.lower() else "exploratory" if "explor" in body.lower() else g["intent"]
        elif name == "SIGNALS":
            g["signals"] = sorted({s for s in re.findall(r"S[1-5]", body.upper())})
        elif name == "COUNTERPOINT":
            g["counterpoints"][str(g["layer"])] = g["counterpoints"].get(str(g["layer"]), 0) + 1
        elif name == "PREDICTIONS":
            g["predictions"] = "off" not in body.lower()
        elif name == "RQ-READY":
            g["pending"].append("rq")
        elif name == "RQ-CANDIDATES":
            g["pending"].append("rq_candidates")
        elif name == "STIMULUS":
            bs_store.add_move(chat_id, chat["space"], "stimulus", {"kind": "analogy" if "analog" in body.lower() else "knowledge",
                                                                   "layer": g["layer"]})
        elif name == "SCOPE" and body and not console:
            g["scope"] = [*[x for x in g["scope"] if x.lower() != body.lower()], body[:200]][-8:]
        elif name == "PREDICT":
            g["predicted_at"] = g["turns"]
        elif name == "DIRECTION" and body and not console:
            said = _direction(chat, g, viewer, body)
            if said:
                effects.append(said)
        elif name == "VERSIONS" and not console:
            g["pending"].append("versions")
        elif name == "END":
            g["ended"] = (body or "user").split()[0].lower()
            g["pending"].append("handoff")
    if g["predicted_at"] != g["turns"] and re.search(r"\bpredict|预测|你猜|你觉得会", clean, re.I):
        g["predicted_at"] = g["turns"]
    if any(name == "RQ-READY" for name, _ in tags) or re.search(r"\bso far\b|where (we|you) (are|have got)|到目前为止|目前我们", clean, re.I):
        g["recapped_at"] = g["turns"]
    if g["layer"] != before:
        g["layer_turns"] = 0
        # leaving Layer 1 means they can state their question in a sentence: it is written down, if it is not yet
        if before == 1 and not g.get("rq") and not g["suggesting"] and not any(p.startswith("rq") for p in g["pending"]):
            g["pending"].append("rq")
        if before <= 2 < g["layer"] and "method" not in g["skeptic"]:
            g["skeptic"].append("method")
            g["pending"].append("skeptic:method")
        if g["layer"] >= 4 > before and "conclusions" not in g["skeptic"]:
            g["skeptic"].append("conclusions")
            g["pending"].append("skeptic:conclusions")
    if noted:
        effects.append([f"Noted {noted} point{'s' if noted > 1 else ''} of yours in your research plan", ""])
    bs_store.set_guide(chat_id, g)
    return clean, effects


def _direction(chat: dict[str, Any], g: dict[str, Any], viewer: str, text: str) -> list[str] | None:
    """A direction the researcher has formed: on their board as a draft idea of theirs (a later one revises it), and
    the conversation's focus, so the other agents can work on it."""
    from aurora.brainstorm import ideas
    text = text.strip()[:600]
    card = bs_store.card(g["direction_card"]) if g.get("direction_card") else None
    if card is not None and card["type"] == "idea":
        if " ".join(card["text"].split()) == " ".join(text.split()):
            return None
        ideas.keep(card, text, viewer=viewer, chat_id=chat["chat_id"])
        said = "Updated your direction on the board"
    else:
        card = bs_store.add_card(chat["space"], viewer, "idea", text, by=viewer, chat_id=chat["chat_id"],
                                 stand="A first version, from the conversation: change it as it changes")
        g["direction_card"] = card["card_id"]
        said = "Wrote your direction on the board"
    if not chat.get("focus"):
        bs_store.update_chat(chat["chat_id"], focus=card["card_id"])
    return [said, card["card_id"]]


def take_pending(chat_id: str) -> list[str]:
    chat = bs_store.chat(chat_id)
    if chat is None:
        return []
    g = guide_of(chat)
    todo, g["pending"] = g["pending"], []
    bs_store.set_guide(chat_id, g)
    return todo


def start(chat_id: str) -> None:
    """The researcher asked the Mentor to guide them (by name, or with the button)."""
    chat = bs_store.chat(chat_id)
    if chat is None:
        return
    g = guide_of(chat)
    if g["ended"]:                      # a new guided dialogue in the same conversation
        g.update(ended="", layer=1, layer_turns=0, signals=[], suggesting=False)
    g["on"] = True
    bs_store.set_guide(chat_id, g)


SKEPTIC = {
    "method": ("The Mentor is guiding the researcher, who has just settled how they would answer their research "
               "question. As the devil's advocate, challenge that methodological choice: its one most serious "
               "problem (a confounder, an alternative explanation, a measurement problem, data they may not get), "
               "as one sharp question. Do not answer it for them, and do not propose another study."),
    "conclusions": ("The Mentor is guiding the researcher, who is now examining their own work critically. As the "
                    "devil's advocate, challenge the assumption their expected conclusion rests on most heavily, and "
                    "say plainly if it is critical (the conclusion would not stand without it). One sharp question; "
                    "do not answer it for them."),
}


# ---------------------------------------------------------------------------
# The research question, written down from the conversation

FINER = ("feasible", "interesting", "novel", "ethical", "relevant")


def _conversation(chat_id: str, viewer: str, n: int = 40) -> str:
    out = []
    for m in bs_store.context(chat_id)[-n:]:
        if m["kind"] != "text" or not m["text"] or m["pending"]:
            continue
        who = "Researcher" if m["who"] == viewer else m["who"] if m["who"] in ("Mentor", "Skeptic", "Methods", "Librarian") else "Colleague"
        out.append(f"{who}: {m['text'][:1500]}")
    return "\n".join(out)


def _plain(x: Any) -> str:
    from aurora.review.llm import unlabel
    return unlabel(x)


def _scored(x: Any) -> dict[str, dict[str, Any]]:
    out = {}
    for k in FINER:
        v = (x or {}).get(k) if isinstance(x, dict) else None
        if isinstance(v, dict):
            try:
                score = max(1, min(5, int(v.get("score", 0))))
            except (TypeError, ValueError):
                score = 0
            out[k] = {"score": score, "why": _plain(v.get("why", ""))[:400]}
    return out


async def draft_question(access: Any, chat_id: str, viewer: str, *, candidates: bool = False, feedback: str = "") -> dict[str, Any]:
    """The research question written down: an RQ Summary from the conversation, or AI-generated candidates."""
    from aurora.review import llm
    chat = bs_store.chat(chat_id) or {}
    g = guide_of(chat)
    prev = g.get("rq") or {}
    ins = "\n".join(f"- ({i['section']}) {i['text']}" for i in bs_store.insights(chat_id))
    said = _conversation(chat_id, viewer)
    amend = (f"\nThe current version:\n{rq_text(prev)}\n\nThe researcher's word on it: {feedback}\n" if prev and feedback else "")
    if candidates:
        body = f"""You are the research question agent of Academic Research Skills, in full mode: the researcher asked \
the system itself to propose candidate research questions. Follow this protocol:

PROTOCOL
{ars_section(RQ_FILE, "## FINER Framework", "## Output Format")}

{ars_section(RQ_FILE, "## Quality Criteria")}
END OF PROTOCOL

The conversation:
{said}
{amend}
Give three to five candidates of varied types, each a single clear sentence ending with "?", built from the \
directions the researcher expressed. Score each on FINER (1 to 5, one sentence why). Give the scope they share. \
Write in the researcher's language.

JSON: {{"candidates": [{{"question": "...", "type": "descriptive|comparative|correlational|causal|evaluative", \
"finer": {{"feasible": {{"score": 4, "why": "..."}}, "interesting": {{...}}, "novel": {{...}}, "ethical": {{...}}, \
"relevant": {{...}}}}}}], "focus": ["..."], "excluded": ["..."], "to_confirm": ["..."]}}"""
        out = await llm.ask(access, "rq_candidates", body, want="candidates")
        cands = [{"question": _plain(c.get("question", "")), "type": str(c.get("type", "")),
                  "finer": _scored(c.get("finer"))}
                 for c in out.get("candidates") or [] if isinstance(c, dict) and c.get("question")][:5]
        rq = {"kind": "candidates", "candidates": cands, "origin": "ai"}
    else:
        body = f"""You are the research question agent of Academic Research Skills, in Socratic mode: the research \
question has converged in a guided dialogue, and you write it down as an RQ Summary. Follow this protocol:

PROTOCOL
{ars_section(RQ_FILE, "## Socratic Mode Branch", "## Quality Criteria")}

{ars_section(RQ_FILE, "## Quality Criteria")}

{ars_section(RQ_FILE, "## FINER Framework", "## Process")}
END OF PROTOCOL

The conversation:
{said}

Insights the researcher reached:
{ins or '(none noted)'}
{amend}
Write the RQ Summary. The question is the one THE RESEARCHER derived, in one sentence ending with "?": keep their \
idea and their terms; you may only tighten the form (and say what you tightened). The FINER self-assessment is what \
they themselves said in the dialogue for each criterion ("" where they said nothing). Focus, excluded and to be \
confirmed come from what they said, as it stands at the end of the conversation (what they have since settled is \
not still open). Then, separately, the formal FINER scoring a full-mode agent would start from (Step 3; 1 to 5, one \
sentence why). "quality": only the quality criteria the question fails, each in a sentence ([] if it passes them \
all). Write every field in the researcher's language.

JSON: {{"question": "...", "tightened": "", "self": {{"feasible": "...", "interesting": "...", "novel": "...", \
"ethical": "...", "relevant": "..."}}, "focus": ["..."], "excluded": ["..."], "to_confirm": ["..."], \
"formal": {{"feasible": {{"score": 4, "why": "..."}}, "interesting": {{...}}, "novel": {{...}}, "ethical": {{...}}, \
"relevant": {{...}}}}, "quality": ["..."]}}"""
        out = await llm.ask(access, "rq_summary", body)
        self_ = out.get("self") if isinstance(out.get("self"), dict) else {}
        rq = {"kind": "summary", "question": _plain(out.get("question", "")), "tightened": _plain(out.get("tightened", "")),
              "self": {k: _plain(self_.get(k, "") or "") for k in FINER}, "formal": _scored(out.get("formal")),
              "quality": [_plain(x) for x in out.get("quality") or [] if _plain(x)][:4], "origin": "you"}
    rq.update(focus=[_plain(x) for x in out.get("focus") or []][:6], excluded=[_plain(x) for x in out.get("excluded") or []][:6],
              to_confirm=[_plain(x) for x in out.get("to_confirm") or []][:4], state="draft", at=time.time(),
              version=int(prev.get("version", 0)) + 1, amended=[*(prev.get("amended") or []), feedback] if feedback else [])
    chat = bs_store.chat(chat_id) or chat
    g = guide_of(chat)
    g["rq"] = rq
    bs_store.set_guide(chat_id, g)
    return rq


WORDS = {
    "en": {"cands": "AI-generated starting points, not yours until you take one:", "yours": "Your research question, as you put it:",
           "tightened": "I only tightened the form:", "said": "What you said about it:", "focus": "Focus:",
           "excluded": "Left out:", "open": "Still open:", "take": "Take one, or tell me what to change.",
           "confirm": "Is this it? Say what to change, or confirm it.", "sep": "; ",
           "finer": {"feasible": "Feasible", "interesting": "Interesting", "novel": "Novel", "ethical": "Ethical",
                     "relevant": "Relevant"}},
    "zh": {"cands": "以下是 AI 提出的起点，在你选中之前都不算你的问题：", "yours": "你的研究问题，用你自己的话说：",
           "tightened": "我只收紧了措辞：", "said": "你在对话里对它的判断：", "focus": "聚焦：", "excluded": "不做：",
           "open": "还没定：", "take": "选一个，或者告诉我要改什么。", "confirm": "是这个问题吗？要改什么直接说，没问题就确认。",
           "sep": "；", "finer": {"feasible": "可行 (Feasible)", "interesting": "有意思 (Interesting)",
                                 "novel": "新颖 (Novel)", "ethical": "伦理 (Ethical)", "relevant": "有意义 (Relevant)"}},
}


def _lang(rq: dict[str, Any]) -> str:
    text = rq.get("question", "") + "".join(c.get("question", "") for c in rq.get("candidates") or [])
    return "zh" if any("\u4e00" <= ch <= "\u9fff" for ch in text) else "en"


def rq_text(rq: dict[str, Any]) -> str:
    """The research question as the Mentor says it in the conversation, in the researcher's language."""
    w = WORDS[_lang(rq)]
    if rq.get("kind") == "candidates":
        lines = [w["cands"]]
        for i, c in enumerate(rq.get("candidates") or []):
            avg = sum(v["score"] for v in c["finer"].values()) / max(1, len(c["finer"]))
            lines.append(f"{i + 1}. {c['question']} (FINER {avg:.1f})")
    else:
        lines = [f"{w['yours']} {rq.get('question', '')}"]
        if rq.get("tightened"):
            lines.append(f"{w['tightened']} {rq['tightened']}")
        said = [f"{w['finer'][k]}: {v}" for k, v in (rq.get("self") or {}).items() if v and k in w["finer"]]
        if said:
            lines.append(w["said"] + "\n" + "\n".join(f"- {x}" for x in said))
    if rq.get("focus"):
        lines.append(f"{w['focus']} " + w["sep"].join(rq["focus"]))
    if rq.get("excluded"):
        lines.append(f"{w['excluded']} " + w["sep"].join(rq["excluded"]))
    if rq.get("to_confirm"):
        lines.append(f"{w['open']} " + w["sep"].join(rq["to_confirm"]))
    lines.append(w["take"] if rq.get("kind") == "candidates" else w["confirm"])
    return "\n\n".join(lines)


def settle(chat_id: str, viewer: str, *, pick: int | None = None) -> dict[str, Any]:
    """The researcher confirms their question (or takes an AI candidate): it goes on the board and in the plan."""
    chat = bs_store.chat(chat_id)
    if chat is None:
        raise ValueError("no such conversation")
    g = guide_of(chat)
    rq = dict(g.get("rq") or {})
    if rq.get("kind") == "candidates":
        cands = rq.get("candidates") or []
        if pick is None or not 0 <= pick < len(cands):
            raise ValueError("take one of the candidates")
        rq.update(question=cands[pick]["question"], formal=cands[pick]["finer"], origin="ai")
    question = rq.get("question", "")
    if not question:
        raise ValueError("there is no research question to confirm")
    vis = "project" if chat["vis"] == "project" else "private"
    card = bs_store.add_card(chat["space"], viewer, "idea", question, by=viewer if rq.get("origin") == "you" else "Mentor",
                             vis=vis, chat_id=chat_id)
    rq.update(state="settled", card_id=card["card_id"], settled_at=time.time())
    g["rq"] = rq
    g["on"] = True
    g["suggesting"] = False
    bs_store.set_guide(chat_id, g)
    bs_store.update_chat(chat_id, focus=card["card_id"])
    return rq


def scope_seed(chat: dict[str, Any] | None) -> dict[str, Any] | None:
    """A confirmed research question in the conversation, for a map of its field to start from."""
    if not chat:
        return None
    rq = guide_of(chat).get("rq") or {}
    if rq.get("state") != "settled":
        return None
    return {"question": rq.get("question", ""), "focus": rq.get("focus") or [], "excluded": rq.get("excluded") or [],
            "to_confirm": rq.get("to_confirm") or [], "origin": rq.get("origin", "you")}


def plan(chat: dict[str, Any]) -> dict[str, Any]:
    """The research plan beside the conversation: the question and the insights, by section."""
    g = guide_of(chat)
    rq = g.get("rq")
    return {"active": active(chat), "ended": g["ended"], "suggesting": g["suggesting"], "started": bool(g["on"]),
            "question": rq, "sections": [{"id": s, "name": SECTION_NAMES[s]} for s in SECTIONS],
            "insights": bs_store.insights(chat["chat_id"])}
