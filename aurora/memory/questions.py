"""The research questions the memory is organised by: those settled with the Mentor, and any written in directly."""

from __future__ import annotations

from typing import Any

from aurora.memory.store import mem_store


def sync_mentor(owner: str) -> None:
    """Questions confirmed in brainstorms with the Mentor become memory questions (once each)."""
    from aurora.brainstorm import mentor
    from aurora.brainstorm.store import bs_store
    rows = bs_store._all("SELECT * FROM bs_chats WHERE owner=? AND guide LIKE '%settled%'", (owner,))
    for ch in rows:
        ch = bs_store._chat(ch)
        rq = mentor.guide_of(ch).get("rq") or {}
        if rq.get("state") == "settled" and rq.get("question") and mem_store.question_by_ref(owner, ch["chat_id"]) is None:
            mem_store.add_question(owner, rq["question"], origin="mentor", ref=ch["chat_id"])


def current(owner: str) -> list[dict[str, Any]]:
    """The person's open questions, oldest first, labelled Q1, Q2, ..."""
    try:
        sync_mentor(owner)
    except Exception:          # brainstorms are optional for the memory
        pass
    return [{**q, "label": f"Q{i + 1}"} for i, q in enumerate(mem_store.questions(owner))]
