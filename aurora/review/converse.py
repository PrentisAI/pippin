"""Maps and deep reads in the conversation: what the Librarian says after each step, and what can be acted on.

A map started from a brainstorm tells that brainstorm what each step found, in a sentence or a short list, with
the step attached so the console can show it (the capabilities, the definition, the progress table, the problems).
A challenge to the map comes back as options to apply. A deep read ends with a question to the researcher: how
would they have come to the idea? Their next message is their guess, and the authors' path follows it.

Messages that carry something to act on have ``kind="action"`` and a ``widget``:

* ``{"type": "proposal", "action": "map_field" | "deep_read", ..., "state": "pending" | "started" | "declined"}``
* ``{"type": "map_step", "map_id", "step"}`` and ``{"type": "map_failed", "map_id"}``
* ``{"type": "challenge", "map_id", "challenge_id", "state": "pending" | "applied"}``
* ``{"type": "guess", "read_id", "state": "waiting" | "answered"}`` and ``{"type": "path", "read_id"}``
"""

from __future__ import annotations

from typing import Any

from aurora.brainstorm.store import bs_store
from aurora.review import fieldmap
from aurora.review.store import review_store

STATUS = {"solved": "solved", "partly": "partly solved", "early": "early attempts", "open": "open"}
CAUSE = {"signal": "no training signal", "data": "missing data", "representation": "the representation",
         "evaluation": "evaluation", "compute": "compute", "assumption": "a shared assumption",
         "definition": "how the problem is defined"}


def estimate_calls(size: int) -> int:
    """About how many model calls a map of ``size`` papers makes (as the console says before starting)."""
    return size + 4 + 3 * (2 + -(-size // 12) + 1) + 7 + 5 + 6


def step_text(step: str, m: dict[str, Any]) -> str:
    """What the Librarian says when a step of the map is done."""
    c = m["content"]
    term = (c.get("need") or {}).get("term") or "it"
    caps = (c.get("need") or {}).get("capabilities") or []
    if step == "need":
        why = ((c["need"].get("why") or [{}])[0].get("text") or "")
        return (f"Why {term} is needed: {why}\n\nI will measure the field by {len(caps)} capabilities: "
                + "; ".join(x["name"] for x in caps) + ".")
    if step == "papers":
        rows = review_store.papers(m["map_id"])
        years = sorted(e["paper"].year for r in rows if r["role"] in ("core", "held_out")
                       and (e := fieldmap._entry(r["paper_id"])) and e["paper"].year)
        held = sum(r["role"] == "held_out" for r in rows)
        bench = sum(r["role"] == "benchmark" for r in rows)
        span = f" from {years[0]} to {years[-1]}" if years else ""
        return (f"I gathered {sum(r['role'] in ('core', 'held_out') for r in rows)} papers{span}, all checked in the indexes"
                + (f", and held {held} out to test the map" if held else "") + (f", plus {bench} benchmarks" if bench else "") + ".")
    if step == "cards":
        rows = review_store.papers(m["map_id"])
        pdf = sum(r["card"].get("source") == "pdf" for r in rows)
        return f"I read them: {pdf} from their PDFs, {sum(bool(r['card']) for r in rows) - pdf} from the abstract only."
    if step == "definition":
        d = c.get("definition") or {}
        comm = [x["community"] for x in d.get("communities") or []]
        return (f"Working definition: “{(d.get('working') or {}).get('text', '')}”\n\nThe field uses the word in "
                f"{len(comm)} ways ({'; '.join(comm)}). If you see it differently, say so here, for example whether "
                f"something counts as {term}, and I will work out what that would change.")
    if step == "frameworks":
        fw = fieldmap.chosen(c)
        if not fw:
            return "The design space could not be settled."
        pending = [p for p in c.get("proposals") or [] if p["state"] == "pending"]
        return (f"The design space has {len(fw['dims'])} dimensions, each a part of the definition: "
                + "; ".join(d["name"] for d in fw["dims"])
                + f". {fw['scores'].get('n_fit', '')} papers fit; held out: {fw['scores'].get('n_held', '')}."
                + (f" {len(pending)} paper(s) do not fit and propose a change to the dimensions." if pending else ""))
    if step == "progress":
        names = {x["id"]: x["name"] for x in caps}
        return "How far the field has come:\n" + "\n".join(
            f"- {names.get(p['capability'], p['capability'])}: {STATUS.get(p['status'], p['status'])}. {p['headline']}"
            for p in c.get("progress") or [])
    if step == "trends":
        t = (c.get("trends") or [{}])[0]
        phases = " → ".join(f"{x['name']} ({x['years']})" for x in c.get("timeline") or [])
        return f"{phases}\n\nWhere it is moving: {t.get('title', '')}. {t.get('text', '')}".strip()
    if step == "assumptions":
        return "Assumed without saying so:\n" + "\n".join(f"- {a['text']}" for a in c.get("assumptions") or [])
    if step == "problems":
        return (f"{len(c.get('problems') or [])} problems are attempted but not solved well:\n"
                + "\n".join(f"- {p['title']} (root cause: {CAUSE.get(p['diagnosis']['cause'], p['diagnosis']['cause'])})"
                            for p in c.get("problems") or [])
                + "\n\nEach can go on the board as an idea, with its evidence.")
    if step == "read_next":
        return ("Worth reading closely: " + ", ".join(f"[{r['key']}]" for r in c.get("read_next") or [])
                + ". Ask me to deep-read one.")
    return ""


def teller(chat_id: str, map_id: str) -> Any:
    """What a map tells the brainstorm it was started from, as its steps finish."""
    def tell(what: str) -> None:
        if not chat_id or bs_store.chat(chat_id) is None:
            return
        m = review_store.map(map_id)
        if m is None:
            return
        if what == "failed":
            bs_store.add_message(chat_id, "Librarian", f"I stopped at “{fieldmap.STEP_NAMES.get(m['step'], m['step'])}”: "
                                 f"{m['error']}", kind="action", widget={"type": "map_failed", "map_id": map_id})
        elif what == "version":
            last = (review_store.versions(map_id) or [{}])[-1]
            if last.get("version", 0) > 1:
                bs_store.add_message(chat_id, "Librarian", f"The map is now version {last['version']}: {last['reason']}.",
                                     kind="action", widget={"type": "map_step", "map_id": map_id, "step": "version"})
        elif what.startswith("challenge:"):
            cid = what.split(":", 1)[1]
            ch = next((x for x in m["content"].get("challenges") or [] if x["id"] == cid), None)
            if ch is None:
                return
            support = " ".join(p["text"] for p in ch["support"][:2])
            text = (f"On “{ch['text']}”: {support}\n\nWays to answer it:\n"
                    + "\n".join(f"- {o['boundary']}" for o in ch["options"]))
            bs_store.add_message(chat_id, "Librarian", text, kind="action",
                                 widget={"type": "challenge", "map_id": map_id, "challenge_id": cid, "state": "pending"})
        elif text := step_text(what, m):
            bs_store.add_message(chat_id, "Librarian", text, kind="action",
                                 widget={"type": "map_step", "map_id": map_id, "step": what})
    return tell


def path_text(read: dict[str, Any]) -> str:
    """The authors' path, told after the researcher's guess."""
    a = read["answers"].get("3") or {}
    if a.get("state") != "done":
        return "The reconstruction of the authors' path is not ready; it shows in the reader once it is."
    support = {"stated": "the authors say so", "consistent": "fits what they write", "unsupported": "my reconstruction only"}
    moves = "\n".join(f"{i + 1}. {m['text']} ({support.get(m.get('support', ''), 'not checked')})"
                      for i, m in enumerate(a.get("moves") or []))
    says = " ".join(p["text"] for p in a.get("authors_say") or [])
    return (f"How the authors may have got there, from what was known before:\n{moves}"
            + (f"\n\nWhat they say themselves: {says}" if says else "")
            + "\n\nThe other eleven questions, from the problem to a follow-up idea, are in the reader.")
