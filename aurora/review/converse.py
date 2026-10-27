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

import logging
from typing import Any

from aurora.brainstorm.store import bs_store
from aurora.review import fieldmap
from aurora.review.store import review_store

logger = logging.getLogger(__name__)

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
        pr = c.get("prisma") or {}
        ident = pr.get("identified") or {}
        return (f"Searching found {sum(ident.values())} records ({', '.join(f'{k} {v}' for k, v in ident.items())}), "
                f"{pr.get('after_dedupe', 0)} distinct papers once checked in the indexes and deduplicated; "
                f"{pr.get('not_found', 0)} named papers were not found and are dropped. Two models now screen them.")
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


def scope_text(c: dict[str, Any]) -> str:
    sc = c.get("scope") or {}
    crit = sc.get("criteria") or {}
    lines = [f"Before I search, this is how I would scope it. The map answers: {sc.get('question', '')}",
             "In scope: " + "; ".join(sc.get("in_scope") or []), "Out of scope: " + "; ".join(sc.get("out_of_scope") or []),
             "Sub-questions:\n" + "\n".join(f"- {x['text']}" for x in sc.get("subquestions") or []),
             "A paper belongs if: " + "; ".join(crit.get("include") or []),
             "It is left out, checked in this order, if:\n" + "\n".join(f"- {e['code']}: {e['text']}" for e in crit.get("exclude") or [])]
    if sc.get("ask"):
        lines.append("Two things I would like to know: " + " ".join(sc["ask"]))
    lines.append("Tell me what to change, or go on.")
    return "\n\n".join(lines)


def corpus_text(map_id: str, c: dict[str, Any]) -> str:
    from aurora.literature.library import library
    pr = c.get("prisma") or {}
    ident = pr.get("identified") or {}
    sc = c.get("scope") or {}
    exc = pr.get("excluded_by_code") or {}
    codes = {e["code"]: e["text"] for e in (sc.get("criteria") or {}).get("exclude") or []}
    rows = review_store.papers(map_id)
    keys = {r["paper_id"]: (library.paper(r["paper_id"]) or {}).get("key", "") for r in rows}
    screening = c.get("screening") or {}
    by_sub = []
    for sub in sc.get("subquestions") or []:
        ks = [keys[r["paper_id"]] for r in rows if r["role"] in ("core", "held_out") and sub["id"] in (screening.get(r["paper_id"], {}).get("subs") or [])]
        by_sub.append(f"- {sub['text']}: " + (", ".join(f"[{k}]" for k in ks) if ks else "none yet"))
    other = [f"[{keys[r['paper_id']]}]" for r in rows if r["role"] in ("survey", "benchmark", "foundation")]
    agreement = pr.get("agreement")
    lines = [
        f"Records identified: {sum(ident.values())} ({', '.join(f'{k} {v}' for k, v in ident.items())}); "
        f"{pr.get('not_found', 0)} named but not found in the indexes, dropped. Distinct papers: {pr.get('after_dedupe', 0)}.",
        f"Two models screened {pr.get('screened', 0)} from title and abstract"
        + (f" (they agreed on {round(agreement * 100)}%)" if agreement is not None else "")
        + f"; {pr.get('conflicts', 0)} disagreements were adjudicated; {pr.get('full_text_assessed', 0)} were read in full text.",
        f"Included: {pr.get('included', 0)}. Excluded: " + (", ".join(f"{codes.get(k, k)} {v}" for k, v in exc.items()) or "none") + ".",
        "In the map, by sub-question:\n" + "\n".join(by_sub),
    ]
    if other:
        lines.append("Also kept, not placed: " + ", ".join(other))
    for a in (c.get("coverage") or {}).get("skew") or []:
        lines.append(f"Skew: {a['n']} of {a['known']} papers are {a['value']} ({a['dimension']}).")
    lines.append("Tell me what to add or drop (by name is fine), or go on.")
    return "\n\n".join(lines)


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
            elif m["content"].get("problems"):
                ask_mentor(chat_id, map_id)
        elif what == "scope_settled":
            sc = m["content"].get("scope") or {}
            bs_store.add_message(chat_id, "Librarian", "I scoped the map from your research question, so I will not ask "
                                 f"about the scope again. It answers: {sc.get('question', '')}\n\nIts parts:\n"
                                 + "\n".join(f"- {x['text']}" for x in sc.get("subquestions") or [])
                                 + "\n\nI am searching now. If it should cover something else, say so when the papers are in.",
                                 kind="action", widget={"type": "map_step", "map_id": map_id, "step": "scope"})
        elif what.startswith("wait:"):
            step = what.split(":", 1)[1]
            for old in bs_store.messages(chat_id):          # an earlier version of this checkpoint is superseded
                w = old.get("widget") or {}
                if w.get("map_id") == map_id and w.get("type") in ("scope", "corpus") and w.get("state") == "waiting":
                    bs_store.update_message(old["msg_id"], widget={**w, "state": "superseded"})
            text = scope_text(m["content"]) if step == "scope" else corpus_text(map_id, m["content"])
            bs_store.add_message(chat_id, "Librarian", text, kind="action",
                                 widget={"type": "scope" if step == "scope" else "corpus", "map_id": map_id,
                                         "state": "waiting", "version": len((m["content"].get("scope") or {}).get("amended") or [])})
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


def ask_mentor(chat_id: str, map_id: str) -> None:
    """A first map is ready: the Mentor asks which of its unsolved problems the researcher cares about, and why."""
    try:
        from aurora.api.routes.brainstorm import mentor_after_map
        mentor_after_map(chat_id, map_id)
    except Exception:
        logger.info("asking the Mentor about map %s failed", map_id, exc_info=True)


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
