"""A run's round table, live in its project's brainstorm space ("Brainstorm with me").

While the run's ideation meets, the meeting appears as a brainstorm shared with the project:

* the chair's steps and every seat's turn are messages (a turn being written shows as it grows);
* a presentation becomes an idea card ("live · from the round table"); a seat's question or concern
  becomes a question on it, a suggestion or build-on becomes an idea that builds on it;
* the chair's decision marks the chosen idea;
* the researcher speaks in it: the remark reaches the meeting before the next turn;
* started from one of the researcher's ideas, the seats develop it, and each new version the researcher writes of
  it reaches the meeting;
* the chair's decision comes back as drafts: the chosen direction, the others presented, and the researcher's own
  idea, each editable; the researcher goes on with one, changed or not, or has the seats go another round.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from aurora.brainstorm.store import bs_store

logger = logging.getLogger(__name__)

# a remark's tag, also as models dress it up: **[CONCERN]**, - [QUESTION]:
_TAG = re.compile(r"^\s*(?:[-*•]\s+)?\**\s*\[(QUESTION|CONCERN|SUGGESTION|BUILD-ON|EVIDENCE|AGREE)\]\s*\**\s*:?\s*(.+)", re.I | re.S)


def _first_sentence(text: str, n: int = 220) -> str:
    text = " ".join(text.split())
    m = re.match(r"(.{8,}?[.?!])\s", text + " ")
    out = m.group(1) if m else text
    return out if len(out) <= n else out[: n - 1] + "…"


class LiveMeeting:
    """Follows one run's ideation events and keeps its brainstorm in step."""

    def __init__(self, campaign_id: str, run_id: str, run_name: str, owner: str, source_card: str = "") -> None:
        self.space, self.run_id, self.run_name, self.owner = campaign_id, run_id, run_name, owner
        self.source_card = source_card
        self.decided: dict[str, Any] = {}
        self.chat_id = ""
        self.ideas: dict[str, str] = {}             # seat -> its idea card
        self.streaming: dict[tuple, str] = {}       # (seat, kind, item, round) -> pending message
        self.item = (0, 0)

    def _chat(self) -> str:
        if not self.chat_id:
            existing = bs_store.chat_for_run(self.run_id)
            ch = existing or bs_store.new_chat(self.space, self.owner, f"{self.run_name} · round table",
                                               vis="project", run_id=self.run_id)
            self.chat_id = ch["chat_id"]
        return self.chat_id

    def _status(self, text: str) -> None:
        bs_store.update_chat(self._chat(), status=text)

    def _card(self, type_: str, text: str, by: str, *, stand: str = "") -> dict[str, Any]:
        return bs_store.add_card(self.space, self.owner, type_, text, by=by, vis="project", stand=stand,
                                 chat_id=self._chat(), live=True)

    async def on_event(self, name: str, data: dict[str, Any]) -> None:
        try:
            self._on(name, data)
        except Exception:
            logger.warning("live brainstorm of %s: %s failed", self.run_id, name, exc_info=True)

    def _on(self, name: str, data: dict[str, Any]) -> None:
        if name == "phase_started" and data.get("phase") == "ideation":
            self._chat()
            self._status("preparing")
            source = bs_store.card(self.source_card) if self.source_card else None
            if source is not None:
                bs_store.add_message(self.chat_id, "Chair", "The round table begins from your idea: the seats develop it "
                                     "into its strongest version rather than proposing others. Speak at any time, or "
                                     "rewrite the idea itself (open it on the right and edit it): the meeting hears each "
                                     "new version before the next turn.", kind="text", effects=[["Starts from", source["card_id"]]])
            else:
                bs_store.add_message(self.chat_id, "Chair", "The round table begins. The seats prepare their ideas; you "
                                     "can speak at any time, and the chair gives you the floor before the next turn.",
                                     kind="text")
        elif name == "ideation_progress":
            step = data.get("step")
            chat = self._chat()
            if step == "agenda_item":
                self.item = (int(data.get("item") or 0), int(data.get("of") or 0))
                self._status(f"item {self.item[0]} of {self.item[1]}")
                bs_store.add_message(chat, "Chair", f"Item {self.item[0]} of {self.item[1]}: {data.get('presenter')} "
                                     "presents; the others make remarks.")
            elif step == "item_notes" and str(data.get("summary") or "").strip():
                bs_store.add_message(chat, "Chair", f"Notes on {data.get('presenter')}: {data.get('summary', '')}")
            elif step == "open_discussion":
                self._status("open discussion")
                bs_store.add_message(chat, "Chair", "Open discussion: anything that cuts across the ideas.")
            elif step == "chair_deciding":
                self._status("the chair decides")
                bs_store.add_message(chat, "Chair", "The chair is deciding the direction.")
            elif step == "decided":
                self._status("decided")
                chosen = str(data.get("chosen", ""))
                self.decided = {"chosen": chosen, "headline": str(data.get("headline", "")),
                                "rationale": str(data.get("rationale", ""))}
                bs_store.add_message(chat, "Chair", f"Decided: {chosen}'s direction — {data.get('headline', '')}. "
                                     f"{data.get('rationale', '')}".strip(),
                                     effects=[["Chose", self.ideas[chosen]]] if chosen in self.ideas else None)
                if chosen in self.ideas:
                    bs_store.update_card(self.ideas[chosen], stand="Chosen by the chair as the run's direction.")
        elif name == "ideation_stream":
            key = (data.get("seat"), data.get("kind"), data.get("item"), data.get("round"))
            text = str(data.get("text") or "")
            if key not in self.streaming:
                self.streaming[key] = bs_store.add_message(self._chat(), str(data.get("seat")), text, pending=True,
                                                           steps=[f"{data.get('model', '')}"])["msg_id"]
            else:
                bs_store.update_message(self.streaming[key], text=text)
        elif name == "ideation_turn":
            self._turn(data)
        elif name == "human_gate_opened" and data.get("source") == "direction":
            self._directions(str(data.get("gate_id", "")))
        elif name == "human_gate_resolved" and data.get("source") == "direction":
            for m in bs_store.messages(self._chat()):
                w = m.get("widget") or {}
                if w.get("type") == "drafts" and w.get("gate_id") == data.get("gate_id") and w.get("state") == "waiting":
                    bs_store.update_message(m["msg_id"], widget={**w, "state": "answered", "action": data.get("action")})
        elif name == "ideation_complete":
            self._status("finished")
            bs_store.add_message(self._chat(), "Chair", f"The meeting is over. The proposal: {data.get('title', '')}. "
                                 "The run goes on to screening and planning.")

    def _directions(self, gate_id: str) -> None:
        """The chair's decision as drafts: the chosen direction first, the others, and the researcher's own idea."""
        chosen = self.decided.get("chosen", "")
        items: list[dict[str, Any]] = []
        if chosen:
            items.append({"text": self.decided.get("headline") or (bs_store.card(self.ideas.get(chosen, "")) or {}).get("text", ""),
                          "seat": chosen, "chosen": True})
        for seat, card_id in self.ideas.items():
            card = bs_store.card(card_id)
            if seat != chosen and card is not None:
                items.append({"text": card["text"], "seat": seat})
        source = bs_store.card(self.source_card) if self.source_card else None
        if source is not None:
            items.append({"text": source["text"], "yours": True, "card_id": source["card_id"]})
        items = [x for x in items if x["text"].strip()]
        bs_store.add_message(self._chat(), "Chair", "The chair's choice is a draft, not the final word. Go on with it, "
                             "or with another direction, changed or not (edit it in place first), or have the seats go "
                             "another round from one. The run waits for you.", kind="action",
                             widget={"type": "drafts", "purpose": "direction", "gate_id": gate_id, "run_id": self.run_id,
                                     "agent": "Chair", "card_id": self.source_card, "drafts": items, "state": "waiting"})

    def _turn(self, data: dict[str, Any]) -> None:
        seat, kind = str(data.get("seat", "")), str(data.get("kind", ""))
        # "HAND: yes" tells the chair who wants to follow up: not for people to read
        content = re.sub(r"\n?\s*HAND:\s*(yes|no)\s*$", "", str(data.get("content") or ""), flags=re.I).strip()
        key = (data.get("seat"), data.get("kind"), data.get("item"), data.get("round"))
        effects: list[list[str]] = []
        if data.get("error"):
            content = f"(This turn failed: {data['error']})"
        elif kind == "presentation":
            idea = self._card("idea", str(data.get("headline") or _first_sentence(content)), seat,
                              stand="Just proposed by the live round table. Nobody has looked at it yet.")
            self.ideas[seat] = idea["card_id"]
            effects.append(["Started an idea", idea["card_id"]])
        elif kind in ("remarks", "open"):
            target = self.ideas.get(str(data.get("item") or ""))
            for para in re.split(r"\n\s*\n", content):
                m = _TAG.match(para)
                if not m or target is None:
                    continue
                tag, body = m.group(1).upper(), _first_sentence(m.group(2), 300)
                if tag in ("QUESTION", "CONCERN"):
                    q = self._card("question", body, seat)
                    bs_store.link(q["card_id"], target, "asks about")
                    effects.append(["Added a question to", target])
                elif tag in ("SUGGESTION", "BUILD-ON"):
                    i = self._card("idea", body, seat)
                    bs_store.link(i["card_id"], target, "builds on")
                    effects.append(["Added an idea building on", target])
        elif kind == "response" and seat in self.ideas and "Updated plan:" in content:
            bs_store.update_card(self.ideas[seat], stand="The presenter revised the plan after the remarks.")
            effects.append(["Updated the assessment of", self.ideas[seat]])
        msg = self.streaming.pop(key, None)
        steps = [str(data.get("model", ""))] if data.get("model") else []
        if msg:
            bs_store.update_message(msg, text=content, pending=False, effects=effects, steps=steps)
        else:
            bs_store.add_message(self._chat(), seat, content, steps=steps, effects=effects)
