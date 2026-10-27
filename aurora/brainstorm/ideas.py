"""Ideas as versions, and the moves of ideation in a brainstorm.

The moves come from Maier, Schneider and Feuerriegel (CHI 2026), who compared ways of co-creating ideas with a
model: a question mode (the model gives a piece of knowledge or an analogy and asks; the person writes versions
of their idea, picks one, and is asked to elaborate it), a suggestion mode, and a model-led mode (the model
rewrites the idea, once or over rounds). The question mode gave better ideas that stayed diverse and that people
felt were theirs; the one-shot rewrite gave good ideas that converged and that people did not feel were theirs,
while the iterative rewrite did not converge. In AURORA every move leaves room for the researcher:

* **Drafts** (model-led): an agent writes two or three drafts when asked; the researcher edits any of them in
  place, takes one, or asks for another round from it (their version first, then new variants).
* **Their own versions** (question mode): after a piece of knowledge or an analogy, they write versions of their
  idea; the console lists them exactly as written, and they pick one to go on with.
* **Versions of an idea**: whatever they take, pick or edit becomes the idea's next version, with who wrote it and
  how much of the text is theirs (the share of characters not taken from the draft it came from).

What happens is also kept as *moves* for evaluation (not shown): stimuli, drafts, what was taken and how much was
changed, the depth to which the researcher elaborated (the paper's five levels), and, for a space, the diversity of
its ideas and the researcher's share of them.
"""

from __future__ import annotations

import logging
import math
import re
from collections import Counter
from difflib import SequenceMatcher
from typing import Any

from aurora.brainstorm.store import bs_store

logger = logging.getLogger(__name__)


def share(base: str, final: str) -> float:
    """How much of ``final`` is the researcher's: the share of its characters not taken from ``base``."""
    final = final.strip()
    if not base.strip() or not final:
        return 1.0
    sm = SequenceMatcher(None, base.strip(), final, autojunk=False)
    same = sum(b.size for b in sm.get_matching_blocks())
    return round(max(0.0, 1 - same / len(final)), 3)


def _origin(base: str, final: str) -> tuple[str, float]:
    if not base.strip():
        return "you", 1.0
    if " ".join(base.split()) == " ".join(final.split()):
        return "ai", 0.0
    return "edited", share(base, final)


def keep(card: dict[str, Any], text: str, *, viewer: str, base: str = "", agent: str = "",
         chat_id: str = "") -> dict[str, Any]:
    """A new version of an idea: the card's text becomes ``text``; ``base`` is the draft it came from, if any."""
    if not bs_store.versions(card["card_id"]):        # the version it had before versions were kept
        mine = card["by"] in ("", card["owner"])
        bs_store.add_version(card["card_id"], card["text"], "you" if mine else "ai", share=1.0 if mine else 0.0,
                             by=card["by"] or card["owner"], chat_id=card.get("chat_id", ""))
    origin, part = _origin(base, text)
    v = bs_store.add_version(card["card_id"], text, origin, share=part, by=agent if origin == "ai" else viewer,
                             chat_id=chat_id)
    bs_store.update_card(card["card_id"], text=text.strip()[:4000])
    return v


def researcher_edit(card: dict[str, Any], text: str, viewer: str) -> dict[str, Any]:
    """The researcher rewrote an idea themselves: its next version, measured against the one before."""
    last = (bs_store.versions(card["card_id"]) or [None])[-1]
    prev_mine = last["origin"] == "you" if last else card["by"] in ("", card["owner"])
    return keep(card, text, viewer=viewer, base="" if prev_mine else card["text"], agent=card["by"])


def history(card_id: str) -> list[dict[str, Any]]:
    return bs_store.versions(card_id)


# ---------------------------------------------------------------------------
# Drafts (model-led, with room to edit and to go another round)


def post_drafts(chat: dict[str, Any], agent: str, drafts: list[str], *, card_id: str = "", note: str = "",
                yours: str = "") -> dict[str, Any]:
    """Drafts in the conversation, each editable; ``yours``: the researcher's version, first (a later round)."""
    texts = [" ".join(str(d).split()) for d in drafts if str(d).strip()][:3]
    items = ([{"text": yours, "yours": True}] if yours else []) + [{"text": t} for t in texts]
    earlier = [m for m in bs_store.messages(chat["chat_id"]) if (m.get("widget") or {}).get("type") == "drafts"
               and m["widget"].get("card_id", "") == card_id and m["widget"].get("purpose", "idea") == "idea"]
    for m in earlier:
        if m["widget"].get("state") == "waiting":
            bs_store.update_message(m["msg_id"], widget={**m["widget"], "state": "superseded"})
    widget = {"type": "drafts", "purpose": "idea", "agent": agent, "card_id": card_id, "round": len(earlier) + 1,
              "drafts": items, "state": "waiting"}
    msg = bs_store.add_message(chat["chat_id"], agent, note.strip(), kind="action", widget=widget)
    bs_store.add_move(chat["chat_id"], chat["space"], "drafts", {"agent": agent, "card_id": card_id,
                                                                 "round": widget["round"], "n": len(texts)})
    return msg


def take(msg: dict[str, Any], i: int, text: str, viewer: str) -> dict[str, Any]:
    """The researcher takes draft ``i`` (``text``: as they edited it): the idea's next version."""
    w = dict(msg["widget"])
    items = w.get("drafts") or []
    if not 0 <= i < len(items):
        raise ValueError("no such draft")
    draft = items[i]["text"]
    final = " ".join((text or draft).split())
    if not final:
        raise ValueError("the draft is empty")
    chat = bs_store.chat(msg["chat_id"]) or {"chat_id": msg["chat_id"], "space": "", "vis": "private"}
    base = "" if items[i].get("yours") else draft
    card = bs_store.card(w.get("card_id") or "") if w.get("card_id") else None
    if card is None:
        origin, part = _origin(base, final)
        card = bs_store.add_card(chat["space"], viewer, "idea", final, by=viewer if origin != "ai" else w.get("agent", ""),
                                 vis="project" if chat.get("vis") == "project" else "private", chat_id=msg["chat_id"])
        v = bs_store.add_version(card["card_id"], final, origin, share=part,
                                 by=viewer if origin != "ai" else w.get("agent", ""), chat_id=msg["chat_id"])
    else:
        v = keep(card, final, viewer=viewer, base=base, agent=w.get("agent", ""), chat_id=msg["chat_id"])
    w.update(state="taken", taken=i, card_id=card["card_id"], origin=v["origin"], share=v["share"], text=final)
    bs_store.update_message(msg["msg_id"], widget=w)
    bs_store.add_move(msg["chat_id"], chat["space"], "taken", {"card_id": card["card_id"], "draft": i, "round": w.get("round"),
                                                               "origin": v["origin"], "share": v["share"],
                                                               "yours": bool(items[i].get("yours"))})
    return {"card_id": card["card_id"], "origin": v["origin"], "share": v["share"], "text": final, "agent": w.get("agent", "")}


# ---------------------------------------------------------------------------
# The researcher's own versions (question mode): listed as written, one picked


def _norm(s: str) -> str:
    return " ".join(s.split()).strip(" \"'“”‘’。.;；")


async def extract_versions(access: Any, chat_id: str, viewer: str) -> list[str]:
    """The versions of their idea the researcher wrote lately, exactly as they wrote them."""
    from aurora.review import llm
    mine = [m["text"] for m in bs_store.messages(chat_id) if m["who"] == viewer and m["kind"] == "text" and m["text"]][-6:]
    if not mine:
        return []
    said = "\n\n".join(f"Message {i + 1}:\n{t}" for i, t in enumerate(mine))
    out = await llm.ask(access, "own_versions", f"""A researcher was asked to write two or three different versions of \
their research idea. Their recent messages:

{said}

List the distinct versions of the idea they wrote, latest messages first if they rewrote one. Copy each EXACTLY as \
they wrote it (a sentence or a few, verbatim, nothing added, nothing fixed); leave out anything that is not a version \
of the idea (remarks, questions, doubts). At most four.

JSON: {{"versions": ["..."]}}""", want="versions", max_tokens=2000)
    blob = _norm("\n".join(mine))
    found = []
    for v in out.get("versions") or []:
        v = _norm(str(v))
        if len(v) >= 6 and v in blob and v not in found:
            found.append(v)
    return found[:4]


def post_choice(chat: dict[str, Any], versions: list[str], *, card_id: str = "") -> dict[str, Any] | None:
    if len(versions) < 2:
        return None
    for m in bs_store.messages(chat["chat_id"]):
        w = m.get("widget") or {}
        if w.get("type") == "choice" and w.get("state") == "waiting":
            bs_store.update_message(m["msg_id"], widget={**w, "state": "superseded"})
    bs_store.add_move(chat["chat_id"], chat["space"], "versions", {"n": len(versions)})
    return bs_store.add_message(chat["chat_id"], "Mentor", "", kind="action",
                                widget={"type": "choice", "versions": versions, "card_id": card_id, "state": "waiting"})


def choose(msg: dict[str, Any], i: int, viewer: str) -> dict[str, Any]:
    w = dict(msg["widget"])
    versions = w.get("versions") or []
    if not 0 <= i < len(versions):
        raise ValueError("no such version")
    text = versions[i]
    chat = bs_store.chat(msg["chat_id"]) or {"chat_id": msg["chat_id"], "space": "", "vis": "private"}
    card = bs_store.card(w.get("card_id") or "") if w.get("card_id") else None
    if card is None or card["owner"] != viewer:
        card = bs_store.add_card(chat["space"], viewer, "idea", text, by=viewer,
                                 vis="project" if chat.get("vis") == "project" else "private", chat_id=msg["chat_id"])
        bs_store.add_version(card["card_id"], text, "you", share=1.0, by=viewer, chat_id=msg["chat_id"])
    else:
        keep(card, text, viewer=viewer, chat_id=msg["chat_id"])
    w.update(state="chosen", chosen=i, card_id=card["card_id"])
    bs_store.update_message(msg["msg_id"], widget=w)
    bs_store.add_move(msg["chat_id"], chat["space"], "chosen", {"card_id": card["card_id"], "of": len(versions)})
    return {"card_id": card["card_id"], "text": text}


# ---------------------------------------------------------------------------
# Evaluation (kept, not shown): elaboration depth, diversity, ownership

DEPTH = """\
Code how far the researcher elaborated their idea in each of their messages that answers an AI turn, with this \
scheme (Maier, Schneider and Feuerriegel, CHI 2026, Appendix E). First find the elaboration codes in the message: \
a feature, a mechanism, a constraint, an audience or context, a guiding principle, an integration (combining or \
restructuring components), a reframing (a new core purpose or audience). Then the level, from the codes and where \
they came from (the AI's turn before it, or the researcher):
1 LLM-led echo: accepts the AI's input as it is; nothing new.
2 Minor elaboration: adds codes, but ones the AI had already given; the core frame unchanged.
3 Substantive elaboration: adds codes of their own that were not in the AI's input.
4 Integrative advancement: combines or restructures components to extend the same core idea, building on the AI's input.
5 Human-led reframing: replaces the core frame or value logic; the idea now serves a different purpose or audience.
Messages that do not work on the idea (greetings, meta remarks) get level 0."""


async def elaboration_depth(access: Any, chat_id: str, viewer: str) -> list[dict[str, Any]]:
    """The depth of each of the researcher's answers to an AI turn (0 to 5), kept as moves."""
    from aurora.review import llm
    msgs = [m for m in bs_store.messages(chat_id) if m["kind"] == "text" and m["text"] and not m["pending"]]
    pairs = []
    for a, b in zip(msgs, msgs[1:], strict=False):
        if a["who"] != viewer and b["who"] == viewer:
            pairs.append((a, b))
    if not pairs:
        return []
    body = "\n\n".join(f"#{i + 1}\nAI ({a['who']}): {a['text'][:1500]}\nResearcher: {b['text'][:1500]}"
                       for i, (a, b) in enumerate(pairs[-30:]))
    out = await llm.ask(access, "elaboration_depth", f"""{DEPTH}

{body}

JSON: {{"levels": [{{"n": 1, "codes": ["feature"], "level": 3}}]}}""", want="levels", max_tokens=4000)
    levels = []
    for x in out.get("levels") or []:
        try:
            n, lv = int(x.get("n")), max(0, min(5, int(x.get("level"))))
        except (TypeError, ValueError, AttributeError):
            continue
        if 1 <= n <= len(pairs[-30:]):
            levels.append({"msg_id": pairs[-30:][n - 1][1]["msg_id"], "level": lv,
                           "codes": [str(c) for c in x.get("codes") or []][:7]})
    chat = bs_store.chat(chat_id) or {"space": ""}
    bs_store.add_move(chat_id, chat["space"], "depth", {"levels": levels})
    return levels


def _bag(text: str) -> Counter:
    words = re.findall(r"[a-z0-9]+|[一-鿿]", text.lower())
    return Counter(words + [a + b for a, b in zip(words, words[1:], strict=False)])


def _cos(a: dict[Any, float] | list[float], b: dict[Any, float] | list[float]) -> float:
    if isinstance(a, list):
        dot = sum(x * y for x, y in zip(a, b, strict=False))           # type: ignore[arg-type]
        na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))  # type: ignore[union-attr]
    else:
        dot = sum(v * b.get(k, 0.0) for k, v in a.items())             # type: ignore[union-attr]
        na, nb = math.sqrt(sum(v * v for v in a.values())), math.sqrt(sum(v * v for v in b.values()))  # type: ignore[union-attr]
    return dot / (na * nb) if na and nb else 0.0


async def _embed(access: Any, texts: list[str], model: str) -> list[list[float]] | None:
    try:
        from aurora.review.llm import _client
        resp = await _client(access).embeddings.create(model=model, input=texts)
        return [list(d.embedding) for d in resp.data]
    except Exception:
        logger.info("embedding ideas with %s failed; using word overlap", model, exc_info=True)
        return None


async def diversity(access: Any, texts: list[str], model: str = "openai/text-embedding-3-large") -> dict[str, Any]:
    """Each idea's distance to the centroid of the others (as in the paper), and the mean."""
    if len(texts) < 2:
        return {"method": "", "mean": None, "per_idea": []}
    vecs = await _embed(access, texts, model)
    method = model
    if vecs is None:
        method = "word overlap"
        bags = [_bag(t) for t in texts]
        out = []
        for i, b in enumerate(bags):
            rest: Counter = Counter()
            for j, o in enumerate(bags):
                if j != i:
                    rest.update(o)
            out.append(1 - _cos(dict(b), {k: v / (len(bags) - 1) for k, v in rest.items()}))
    else:
        out = []
        for i, v in enumerate(vecs):
            others = [o for j, o in enumerate(vecs) if j != i]
            centroid = [sum(c) / len(others) for c in zip(*others, strict=False)]
            out.append(1 - _cos(v, centroid))
    return {"method": method, "mean": round(sum(out) / len(out), 4), "per_idea": [round(x, 4) for x in out]}


def ownership(cards: list[dict[str, Any]]) -> dict[str, Any]:
    """How much of the ideas' current versions is the researcher's (cards without versions count by who made them)."""
    shares = []
    for c in cards:
        vs = bs_store.versions(c["card_id"])
        shares.append(vs[-1]["share"] if vs else (1.0 if c["by"] in ("", c["owner"]) else 0.0))
    return {"ideas": len(shares), "mean_share": round(sum(shares) / len(shares), 3) if shares else None}
