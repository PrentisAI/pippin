"""Speaking to Pippin: hold a key, say it, let go.

What you say is understood together with what you are doing at that moment, so "this paragraph", "this figure",
"this paper", "the one before" and "the second one" (of the digest you just heard) mean something concrete:

- this sentence / paragraph: the selection; without one, the text around where you are;
- this figure: a screenshot of that one window, taken when you spoke (only then, only that window);
- this paper: what is open in front;
- the one before: what you had open before it (the last hours, kept in memory only);
- the second one: the second item of the digest you last heard.

Answers are short when spoken and complete on the panel, with their sources (citations are hard to read aloud).
Writes are confirmed in words ("added to your reading list; say undo to take it back") when you allowed them;
when a write is set to "ask", Pippin asks and your next words decide; when it is off, Pippin says where to turn it
on. Large writes (the manuscript) are never made by voice: they are shown on the panel.

Speech recognition runs on your machine: the desktop app runs whisper.cpp itself and sends the words, or the
console runs the command in ``AURORA_WHISPER`` on the recording (``{audio}`` stands for the file). Recordings are
not kept.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shlex
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from aurora.memory import ingest, surface
from aurora.memory.store import mem_store

logger = logging.getLogger(__name__)

WHISPER_ENV = "AURORA_WHISPER"
WHISPER_LIVE_ENV = "AURORA_WHISPER_LIVE"   # a faster command for the words shown while speaking (a small model)
TURNS = 20
_digests: dict[str, dict[str, Any]] = {}     # owner -> the digest last heard (its numbered items)
_undo: dict[str, list[dict[str, Any]]] = {}  # owner -> what voice did, to take back (latest last)
_pending: dict[str, dict[str, Any]] = {}     # owner -> a write waiting for a yes
_turns: dict[str, list[dict[str, Any]]] = {}  # owner -> what was said and answered lately (for the panel)
INTENTS = ("ask", "compare", "save", "note", "toread", "sink", "raise", "digest", "cite", "deep_read", "undo",
           "confirm", "cancel", "unknown")


# ---------------------------------------------------------------------------
# Hearing


def can_hear() -> bool:
    """Whether this console recognises speech: a whisper command is set and its program is installed."""
    cmd = os.environ.get(WHISPER_ENV, "").strip()
    try:
        return bool(cmd) and shutil.which(shlex.split(cmd)[0]) is not None
    except ValueError:
        return False


async def transcribe(data: bytes, suffix: str = ".wav", *, live: bool = False, fast: bool = False) -> str:
    """The words of a recording, by the local whisper command (``AURORA_WHISPER``). *live*: the words so far
    while someone speaks, with whisper's window cut to the recording (it otherwise works on 30 s whatever the
    length); *fast*: by the faster command when there is one (``AURORA_WHISPER_LIVE``, a small model)."""
    cmd = (os.environ.get(WHISPER_LIVE_ENV, "").strip() if fast else "") or os.environ.get(WHISPER_ENV, "").strip()
    if not cmd:
        raise ValueError("Speech recognition is not set up on this console: set AURORA_WHISPER to a whisper.cpp "
                         "command (for example: whisper-cli -m ~/models/ggml-large-v3-turbo.bin -nt -l auto -f {audio}), "
                         "or let the desktop app recognise speech itself")
    with tempfile.TemporaryDirectory(prefix="aurora-voice-") as tmp:
        path = Path(tmp) / f"said{suffix if suffix.startswith('.') else '.' + suffix}"
        path.write_bytes(data)
        if path.suffix != ".wav" and shutil.which("ffmpeg"):      # whisper.cpp reads 16 kHz WAV
            wav = Path(tmp) / "said.wav"
            p = await asyncio.create_subprocess_exec("ffmpeg", "-y", "-loglevel", "error", "-i", str(path), "-ar", "16000",
                                                     "-ac", "1", str(wav))
            await asyncio.wait_for(p.wait(), 60)
            if wav.is_file():
                path = wav
        args = [a.replace("{audio}", str(path)) for a in shlex.split(cmd)]
        if not any(str(path) in a for a in args):
            args.append(str(path))
        if live and path.suffix == ".wav" and "--audio-ctx" not in args:
            # 50 frames a second of audio, and a margin: shorter than the recording, whisper repeats itself
            seconds = max(0, path.stat().st_size - 44) / 32000
            args[1:1] = ["--audio-ctx", str(min(1500, int(seconds * 50) + 64))]
        p = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await asyncio.wait_for(p.communicate(), 180)
        if p.returncode != 0:
            raise ValueError(f"speech recognition failed: {err.decode(errors='replace')[-300:]}")
    text = re.sub(r"\[\d\d:\d\d[:.\d]*\s*-->\s*[\d:.]+\]\s*", "", out.decode(errors="replace"))
    return " ".join(text.split())


# ---------------------------------------------------------------------------
# What "this", "the one before" and "the second one" are


def scene(owner: str) -> dict[str, Any]:
    cur = surface.current_context(owner) or {}
    recent = [c for c in reversed(surface.recent_contexts(owner))
              if (c.get("url"), c.get("title")) != (cur.get("url"), cur.get("title"))][:5]
    saved = [s for s in mem_store.sources(owner, 8) if s["state"] == "done"]
    d = _digests.get(owner)
    return {"current": cur, "recent": recent, "saved": saved,
            "digest": d["items"] if d and time.time() - d["at"] < 12 * 3600 else [],
            "pending": _pending.get(owner)}


def _scene_text(sc: dict[str, Any]) -> str:
    c = sc["current"]
    lines = []
    if c:
        lines.append(f"In front now ({c.get('app') or 'an app'}): {c.get('title') or c.get('url')}"
                     + (f" — page {c['page']}" if c.get("page") else ""))
        if c.get("selection"):
            lines.append(f"Selected: {c['selection'][:1200]}")
        elif c.get("text"):
            lines.append(f"Text around where they are: {c['text'][:1500]}")
    else:
        lines.append("Nothing is known to be open in front.")
    for i, r in enumerate(sc["recent"], 1):
        lines.append(f"recent:{i} (had open before; recent:1 is the one just before) {r.get('title') or r.get('url')}")
    for s in sc["saved"][:5]:
        lines.append(f"source:{s['source_id']} (saved {time.strftime('%a %H:%M', time.localtime(s['created_at']))}) "
                     f"{s['title'] or s['url']}")
    for it in sc["digest"]:
        lines.append(f"digest:{it['n']} (item {it['n']} of the digest they last heard) {it['text']}")
    if sc["pending"]:
        lines.append(f"Waiting for a yes or no: {sc['pending']['question']}")
    return "\n".join(lines)


def _target(owner: str, sc: dict[str, Any], ref: str) -> dict[str, Any] | None:
    """A reference the model gave ("this", "recent:1", "digest:2", "source:s_…") as the thing it is."""
    ref = (ref or "").strip()
    c = sc["current"]
    if ref in ("selection", "this", "page", "paper", "figure", "") and c:
        return {"kind": "page", "title": c.get("title", ""), "url": c.get("url", ""), "paper_id": c.get("paper_id", ""),
                "text": c.get("selection") or c.get("text", ""), "selection": c.get("selection", "")}
    if ref.startswith("recent:"):
        try:
            r = sc["recent"][int(ref.split(":")[1]) - 1]
        except (ValueError, IndexError):
            return None
        return {"kind": "page", "title": r.get("title", ""), "url": r.get("url", ""), "paper_id": r.get("paper_id", ""),
                "text": r.get("selection") or r.get("text", ""), "selection": ""}
    if ref.startswith("digest:"):
        n = ref.split(":")[1]
        it = next((x for x in sc["digest"] if str(x["n"]) == n), None)
        return {"kind": "digest", **it} if it else None
    if ref.startswith("source:"):
        s = mem_store.source(ref.split(":", 1)[1])
        if s and s["owner"] == owner:
            return {"kind": "source", "source_id": s["source_id"], "title": s["title"], "url": s["url"],
                    "paper_id": s["paper_id"], "text": mem_store.snapshot(s["source_id"])[:6000]}
    return None


async def _paper_of(t: dict[str, Any]) -> dict[str, Any] | None:
    """The Library entry a target is, when it is a paper."""
    from aurora.literature.library import library
    from aurora.literature.records import clean_arxiv, clean_doi
    if t.get("paper_id") and (e := library.paper(t["paper_id"])):
        return e
    from aurora.memory import card
    url = t.get("url", "")
    doi, arxiv = clean_doi(url), clean_arxiv(url) if "arxiv" in url else ""
    if not (doi or arxiv or t.get("title")):
        return None
    return await card.resolve(doi=doi, arxiv=arxiv, title="" if doi or arxiv else t.get("title", ""))


def _text_of(t: dict[str, Any]) -> str:
    if t.get("text"):
        return t["text"]
    if t.get("unit_id") and (u := mem_store.unit(t["unit_id"])):
        return f"{u['claim']}\n{u['gist']}"
    if t.get("source_id"):
        return mem_store.snapshot(t["source_id"])[:6000]
    return t.get("title", "")


# ---------------------------------------------------------------------------
# Understanding and doing


async def hear(access: Any, models: dict[str, str], owner: str, said: str, *, screenshot: str = "") -> dict[str, Any]:
    """What was said, understood in its scene and done. Returns what to say, what to show, and how to undo it."""
    from aurora.review import llm
    sc = scene(owner)
    said = said.strip()
    if not said:
        return _turn(owner, said, "unknown", "I did not hear anything.", "")
    out = await llm.ask(access.with_model(models["memory"]), "memory_voice", f"""A researcher held a key and said \
something to their research assistant. Work out what they want and what their words point at.

They said: "{said}"

What is around them now:
{_scene_text(sc)}

intent, one of: ask (a question: about what is in front, or to their notebook), compare (how two things differ), \
save (keep what is in front in their notebook), note (a thought of theirs to keep: "note that…", "记一下…"), toread \
(put a paper on their reading list), sink (they do not want to see it again), raise (bring it back), digest (read \
them today's digest), cite (find a citation for the sentence they are writing), deep_read (read a paper closely with \
them), undo (take back what was just done), confirm (yes, to the question waiting), cancel (no), unknown.

targets: what the words point at, as refs from the list above: "this" (what is in front: "this paragraph", "this \
paper", "这段", "这篇"), "figure" ("this figure", "这张图"), "recent:1" ("the one before", "刚才那篇"), "digest:2" \
("the second one", "第二篇"), "source:<id>" (something they saved, e.g. "the one I read last week"). Several for \
compare. question: the question, made explicit (with what the words point at spelled out). note: the thought, in \
their words. lang: the language they spoke (zh or en).

JSON: {{"intent": "...", "targets": ["this"], "question": "...", "note": "...", "lang": "zh"}}""", max_tokens=3000)
    intent = out.get("intent") if out.get("intent") in INTENTS else "unknown"
    zh = (out.get("lang") or "") == "zh" or bool(re.search(r"[\u4e00-\u9fff]", said))
    refs = [str(r) for r in out.get("targets") or []][:4]
    targets = [t for r in refs if (t := _target(owner, sc, r))]
    figure = "figure" in refs
    try:
        return await _do(access, models, owner, said, intent, targets, out, zh, figure, screenshot)
    except Exception as exc:
        logger.info("voice %s failed", intent, exc_info=True)
        return _turn(owner, said, intent, "出了点问题，详情在面板上。" if zh else "Something went wrong; the panel has the details.",
                     str(exc)[:400])


def _say(zh: bool, cn: str, en: str) -> str:
    return cn if zh else en


async def _do(access: Any, models: dict[str, str], owner: str, said: str, intent: str, targets: list[dict[str, Any]],
              out: dict[str, Any], zh: bool, figure: bool, screenshot: str) -> dict[str, Any]:
    from aurora.auth import writes
    if intent in ("confirm", "cancel"):
        p = _pending.pop(owner, None)
        if p is None:
            return _turn(owner, said, intent, _say(zh, "现在没有等你确认的事。", "Nothing is waiting for a yes."), "")
        if intent == "cancel":
            return _turn(owner, said, intent, _say(zh, "好，不写了。", "All right, I left it."), "")
        return await _write(owner, said, p, zh)
    if intent == "undo":
        return await _undo_last(owner, said, zh)
    if intent == "digest":
        d = spoken_digest(owner, zh)
        return _turn(owner, said, intent, d["speech"], d["text"], items=d["items"])
    if intent == "ask":
        return await _answer(access, models, owner, said, out.get("question") or said, targets, zh,
                             [screenshot] if figure and screenshot else [])
    if intent == "compare":
        if len(targets) < 2:
            return _turn(owner, said, intent, _say(zh, "要比较哪两个？没听出来。", "Which two? I could not tell."), "")
        return await _compare(access, models, owner, said, targets[:2], zh)
    if intent == "save":
        t = targets[0] if targets else None
        if t is None or not (t.get("url") or t.get("text")):
            return _turn(owner, said, intent, _say(zh, "前台没有可存的东西。", "There is nothing in front to save."), "")
        src = ingest.start(access, models, owner, url=t.get("url", ""), text=t.get("text", ""), title=t.get("title", ""),
                           selection=t.get("selection", ""), via="voice")
        uid = _push_undo(owner, {"kind": "source", "source_id": src["source_id"], "what": t.get("title") or "it"})
        return _turn(owner, said, intent, _say(zh, "存好了，正在整理。要撤销就说撤销。", "Saved; I am reading it. Say undo to take it back."),
                     t.get("title", ""), undo_id=uid)
    if intent == "note":
        return await _note(access, models, owner, said, out.get("note") or said, targets, zh)
    if intent == "toread":
        t = targets[0] if targets else None
        e = await _paper_of(t) if t else None
        if e is None:
            return _turn(owner, said, intent, _say(zh, "没认出是哪篇论文。", "I could not tell which paper."), "")
        from aurora.literature.library import library
        added = library.add_item("", e["paper_id"], source="to_read", added_by="voice", owner=owner)
        uid = _push_undo(owner, {"kind": "library", "paper_id": e["paper_id"], "added": added, "what": e["paper"].title})
        speech = _say(zh, f"已加入待读：{_short(e['paper'].title)}。要撤销就说撤销。",
                      f"Added to your reading list: {_short(e['paper'].title)}. Say undo to take it back.")
        res = _turn(owner, said, intent, speech, e["paper"].title, undo_id=uid)
        z = await _zotero_item(owner, e["paper_id"])
        if z is not None:            # the Zotero item too, as far as allowed
            lv = writes.levels(owner).get("zotero.toread", "off")
            w = {"write": "zotero.toread", "paper_id": e["paper_id"], "item": z, "title": e["paper"].title,
                 "question": _say(zh, "要在 Zotero 里也标成待读吗？", "Mark it to read in Zotero too?")}
            if lv == "auto":
                return await _write(owner, said, w, zh, prefix=res["speech"])
            if lv == "ask":
                _pending[owner] = w
                res["speech"] += " " + w["question"]
        return res
    if intent in ("sink", "raise"):
        t = targets[0] if targets else None
        unit_id = t.get("unit_id") if t else ""
        if not unit_id:
            return _turn(owner, said, intent, _say(zh, "没认出是哪条。", "I could not tell which one."), "")
        mem_store.update_unit(unit_id, sunk=int(intent == "sink"))
        mem_store.mark(unit_id, owner, "sunk" if intent == "sink" else "raised")
        uid = _push_undo(owner, {"kind": "mark", "unit_id": unit_id, "was": "raised" if intent == "sink" else "sunk"})
        return _turn(owner, said, intent, _say(zh, "好，它沉下去了。" if intent == "sink" else "捞回来了。",
                                               "Done, it sinks." if intent == "sink" else "Raised again."), "", undo_id=uid)
    if intent == "cite":
        t = targets[0] if targets else None
        sentence = (t or {}).get("selection") or (t or {}).get("text", "")[:600]
        if not sentence:
            return _turn(owner, said, intent, _say(zh, "选中那句话再说一次。", "Select the sentence and say it again."), "")
        from aurora.memory import cite
        res = await cite.suggest(access, models, owner, sentence)
        n = len(res["candidates"])
        text = "\n\n".join(f"{c['title']} ({c['byline']})\n“{c['quote']}”\n\\cite{{{c['key']}}}" for c in res["candidates"])
        return _turn(owner, said, intent, _say(zh, f"你的库里有 {n} 篇能支撑这句话，在面板上。" if n else "你的库里没有能支撑这句话的。",
                                               f"{n} papers in your library back it; they are on the panel." if n
                                               else "Nothing in your library backs it."),
                     text or res.get("note", ""))
    if intent == "deep_read":
        t = targets[0] if targets else None
        e = await _paper_of(t) if t else None
        if e is None:
            return _turn(owner, said, intent, _say(zh, "没认出是哪篇论文。", "I could not tell which paper."), "")
        from aurora.api.routes.review import start_read
        await start_read(e["paper_id"], "", start=True)
        return _turn(owner, said, intent, _say(zh, "开始精读了，打开控制台的阅读器就能接着读。",
                                               "The close reading has started; open it in the console's reader."),
                     e["paper"].title)
    return _turn(owner, said, "unknown", _say(zh, "没听懂，可以换个说法。", "I did not understand; try saying it another way."), "")


async def thought(access: Any, models: dict[str, str], owner: str, text: str) -> dict[str, Any]:
    """A thought typed with the quick-note key: kept as your note, about what is in front (on its Zotero item too,
    as far as allowed), like one said aloud."""
    sc = scene(owner)
    t = _target(owner, sc, "this") if sc["current"] else None
    zh = bool(re.search(r"[\u4e00-\u9fff]", text))
    return await _note(access, models, owner, text, text.strip(), [t] if t else [], zh)


def _short(title: str) -> str:
    return title if len(title) <= 60 else title[:57] + "…"


async def _answer(access: Any, models: dict[str, str], owner: str, said: str, question: str,
                  targets: list[dict[str, Any]], zh: bool, images: list[str]) -> dict[str, Any]:
    """An answer from what is in front and from the notebook, every point with its source; none without one."""
    from aurora.review import llm
    units = mem_store.units(owner, include_sunk=False)
    near: list[dict[str, Any]] = []
    if units:
        vec = (await ingest.embed(access, models["embedding"], [question + "\n" + "\n".join(_text_of(t)[:500] for t in targets)]))[0]
        uv = await ingest.unit_vectors(access, models["embedding"], units)
        near = [u for u in sorted(units, key=lambda u: -ingest.cosine(vec, uv.get(u["unit_id"], [0.0])))[:8]
                if ingest.relevance(ingest.cosine(vec, uv.get(u["unit_id"], [0.0]))) >= 0.3]
    front = "\n\n".join(f"[t{i}] {t.get('title', '')}\n{_text_of(t)[:4000]}" for i, t in enumerate(targets))
    mem = "\n".join(f"[{u['unit_id']}] {u['claim']} — " + " | ".join(f"“{e['quote'][:200]}” ({e['title'] or e['url']})"
                                                                    for e in mem_store.evidence(u["unit_id"])[:2])
                    for u in near)
    if not front and not mem and not images:
        return _turn(owner, said, "ask", _say(zh, "我手头没有能回答这个的出处。", "I have no source to answer that from."), "")
    out = await llm.ask(access.with_model(models["memory"]), "memory_voice_answer", f"""A researcher asked aloud: \
"{question}"

What they have in front of them:
{front or '(nothing)'}
{'(A screenshot of the window they meant is attached.)' if images else ''}

From their notebook (claims they saved, with the sources' words):
{mem or '(nothing relevant)'}

Answer only from these. Every point names where it comes from: [t0] for what is in front, a claim id for the \
memory. If they do not answer it, say so; do not guess. Give "speech": one or two short sentences to say aloud \
(no citations, no lists), and "answer": the full answer for the screen, with the sources in brackets. Write in \
{'Chinese' if zh else 'English'}.

JSON: {{"speech": "...", "answer": "...", "units": ["u_..."]}}""", max_tokens=5000, images=images or None)
    by = {u["unit_id"]: u for u in near}
    cited = [by[i] for i in out.get("units") or [] if i in by]
    items = [{"unit_id": u["unit_id"], "claim": u["claim"],
              "sources": [{"title": e["title"], "url": e["url"], "quote": e["quote"]} for e in mem_store.evidence(u["unit_id"])[:3]]}
             for u in cited]
    return _turn(owner, said, "ask", llm.unlabel(out.get("speech", "")), llm.unlabel(out.get("answer", "")), items=items)


async def _compare(access: Any, models: dict[str, str], owner: str, said: str, ts: list[dict[str, Any]], zh: bool) -> dict[str, Any]:
    speech, answer = await compare(access, models, ts, zh, asked=said)
    return _turn(owner, said, "compare", speech, answer)


def target_of(owner: str, *, unit_id: str = "", source_id: str = "", paper_id: str = "") -> dict[str, Any] | None:
    """Something to compare, named by its unit, source or paper."""
    from aurora.literature.library import library
    if unit_id and (u := mem_store.unit(unit_id)) and u["owner"] == owner:
        ev = mem_store.evidence(unit_id)
        return {"kind": "unit", "unit_id": unit_id, "title": u["claim"],
                "text": f"{u['claim']}\n{u['gist']}\n" + "\n".join(f"“{e['quote']}” ({e['title']})" for e in ev[:4])}
    if source_id and (s := mem_store.source(source_id)) and s["owner"] == owner:
        return {"kind": "source", "source_id": source_id, "title": s["title"], "text": mem_store.snapshot(source_id)[:6000]}
    if paper_id and (e := library.paper(paper_id)):
        return {"kind": "paper", "paper_id": paper_id, "title": e["paper"].title,
                "text": f"{e['paper'].title}\n{e['paper'].abstract or ''}"}
    return None


async def compare(access: Any, models: dict[str, str], ts: list[dict[str, Any]], zh: bool, *,
                  asked: str = "") -> tuple[str, str]:
    """How two things differ, only from their text, each difference with the words it rests on: (short, full)."""
    from aurora.review import llm
    body = "\n\n".join(f"[{i}] {t.get('title', '')}\n{_text_of(t)[:5000]}" for i, t in enumerate(ts))
    out = await llm.ask(access.with_model(models["memory"]), "memory_voice_compare", f"""A researcher asked: \
"{asked or 'How do these two differ?'}". How do these two differ (what they do, what they find, where they disagree)? \
Only from their text; quote the words a difference rests on.

{body}

"speech": one or two short sentences to say aloud; "answer": the full comparison for the screen, each point with its \
quote. Write in {'Chinese' if zh else 'English'}.

JSON: {{"speech": "...", "answer": "..."}}""", max_tokens=5000)
    return llm.unlabel(out.get("speech", "")), llm.unlabel(out.get("answer", ""))


async def _note(access: Any, models: dict[str, str], owner: str, said: str, thought: str, targets: list[dict[str, Any]],
                zh: bool) -> dict[str, Any]:
    """A thought kept: in the notebook as your own note; on the paper's Zotero item too, as far as allowed."""
    from aurora.auth import writes
    t = targets[0] if targets else None
    about = (t or {}).get("title", "")
    src = mem_store.add_source(owner, platform="note", url="", title=f"Thought on {about}" if about else "A thought",
                               via="voice")
    task = asyncio.create_task(ingest.process(access, models, src["source_id"],
                                              text=thought + (f"\n\n(About: {about})" if about else "")))
    ingest._tasks.add(task)
    task.add_done_callback(ingest._tasks.discard)
    uid = _push_undo(owner, {"kind": "source", "source_id": src["source_id"], "what": "the thought"})
    res = _turn(owner, said, "note", _say(zh, "记下了。", "Noted."), thought, undo_id=uid)
    e = await _paper_of(t) if t else None
    z = await _zotero_item(owner, e["paper_id"]) if e else None
    if z is None:
        return res
    lv = writes.levels(owner).get("zotero.note", "off")
    w = {"write": "zotero.note", "paper_id": e["paper_id"], "item": z, "title": e["paper"].title, "note": thought,  # type: ignore[index]
         "question": _say(zh, "要存成这篇论文的 Zotero 笔记吗？", "Keep it as a Zotero note on this paper?")}
    if lv == "auto":
        return await _write(owner, said, w, zh, prefix=res["speech"])
    if lv == "ask":
        _pending[owner] = w
        res["speech"] += " " + w["question"]
    else:
        res["speech"] += " " + _say(zh, "只记在 Pippin 里；要同时写进 Zotero 笔记，在账号按钮 → Pippin 可以写什么 里打开。",
                                   "Kept in Pippin only; to write it to Zotero as well, allow it under the account button → What Pippin may write.")
    return res


async def _zotero_item(owner: str, paper_id: str) -> dict[str, Any] | None:
    from aurora.literature.zotero import zotero_store
    link = zotero_store.link(owner)
    if link is None or link["method"] != "web":
        return None
    return zotero_store.item_of_paper(owner, paper_id)


async def _write(owner: str, said: str, w: dict[str, Any], zh: bool, prefix: str = "") -> dict[str, Any]:
    """A write into Zotero that was allowed (or just confirmed), logged, and undoable by voice."""
    import html as h

    from aurora.auth.writes import write_log
    from aurora.literature.zotero import ZoteroError, client_for
    client = client_for(owner)
    item = w["item"]
    try:
        if w["write"] == "zotero.note":
            made = await client.create_items(item["library"], [{"itemType": "note", "parentItem": item["item_key"],
                                                                "note": f"<p>{h.escape(w['note'])}</p><p><i>Said to Pippin "
                                                                        f"{time.strftime('%Y-%m-%d %H:%M')}</i></p>",
                                                                "tags": [{"tag": "aurora"}]}])
            key = next((v["key"] for v in made.values() if v.get("key")), "")
            if not key:
                raise ZoteroError(next((v["error"] for v in made.values() if v.get("error")), "Zotero did not take the note"))
            undo = {"kind": "zotero_note", "library": item["library"], "item_key": key}
            speech = _say(zh, "已存成 Zotero 笔记。要撤销就说撤销。", "Kept as a Zotero note. Say undo to take it back.")
        else:
            got = await client.item(item["library"], item["item_key"])
            if got is None:
                raise ZoteroError("the item is no longer in Zotero")
            tags = [*((got.get("data") or {}).get("tags") or []), {"tag": "to-read"}]
            await client.patch(item["library"], item["item_key"], {"tags": tags}, int(got.get("version") or 0))
            undo = {"kind": "zotero_tag", "library": item["library"], "item_key": item["item_key"], "tag": "to-read"}
            speech = _say(zh, "Zotero 里也标成待读了。", "Marked to read in Zotero too.")
    except ZoteroError as exc:
        return _turn(owner, said, "write", f"{prefix} " + _say(zh, f"写 Zotero 没成功：{exc}", f"Writing to Zotero failed: {exc}"), "")
    log = write_log.record(owner, w["write"], {"paper_id": w["paper_id"], "via": "voice"}, undo)
    uid = _push_undo(owner, {"kind": "write", "log_id": log})
    return _turn(owner, said, "write", f"{prefix} {speech}".strip(), w["title"], undo_id=uid)


def _push_undo(owner: str, what: dict[str, Any]) -> str:
    uid = uuid.uuid4().hex[:10]
    _undo.setdefault(owner, []).append({**what, "undo_id": uid, "at": time.time()})
    _undo[owner] = _undo[owner][-20:]
    return uid


async def undo(owner: str, undo_id: str = "") -> bool:
    """Take back what voice did (the latest, or the one named)."""
    from aurora.auth import writes
    from aurora.literature.library import library
    stack = _undo.get(owner) or []
    it = next((x for x in reversed(stack) if not undo_id or x["undo_id"] == undo_id), None)
    if it is None:
        return False
    stack.remove(it)
    if it["kind"] == "source":
        mem_store.retire_source(it["source_id"])
    elif it["kind"] == "library" and it["added"]:
        library.remove_item("", it["paper_id"], owner=owner)
    elif it["kind"] == "mark":
        mem_store.update_unit(it["unit_id"], sunk=int(it["was"] == "sunk"))
        mem_store.mark(it["unit_id"], owner, it["was"])
    elif it["kind"] == "write":
        await writes.undo(owner, it["log_id"])
    return True


async def _undo_last(owner: str, said: str, zh: bool) -> dict[str, Any]:
    if not await undo(owner):
        return _turn(owner, said, "undo", _say(zh, "没有可以撤销的。", "There is nothing to undo."), "")
    return _turn(owner, said, "undo", _say(zh, "撤销了。", "Undone."), "")


def _turn(owner: str, said: str, intent: str, speech: str, text: str, *, items: list[dict[str, Any]] | None = None,
          undo_id: str = "") -> dict[str, Any]:
    t = {"turn_id": uuid.uuid4().hex[:10], "at": time.time(), "said": said, "intent": intent, "speech": speech,
         "text": text, "items": items or [], "undo_id": undo_id, "waiting": bool(_pending.get(owner))}
    _turns.setdefault(owner, []).append(t)
    _turns[owner] = _turns[owner][-TURNS:]
    return t


def turns(owner: str, limit: int = 5) -> list[dict[str, Any]]:
    return list(reversed(_turns.get(owner, [])))[:limit]


# ---------------------------------------------------------------------------
# The digest, to listen to


def spoken_digest(owner: str, zh: bool | None = None) -> dict[str, Any]:
    """Today's digest as something to say: numbered, so "the second one" can be acted on afterwards."""
    from aurora.memory import relate
    if zh is None:
        zh = surface.language(owner).startswith("Chinese")
    d = relate.digest(owner, 24)
    items: list[dict[str, Any]] = []
    for a in d["alerts"]:
        items.append({"n": len(items) + 1, "kind": "alert", "text": f"{a['title']}: {a['body']}"})
    for p in _feed_papers(owner):
        items.append({"n": len(items) + 1, "kind": "paper", "text": p["title"], "title": p["title"], "url": p["url"],
                      "why": p["why"]})
    for g in d["groups"]:
        for c in g["conflicts"][:2]:
            items.append({"n": len(items) + 1, "kind": "conflict", "text": f"{c['a']} ⟷ {c['b']}", "unit_id": c["a_id"]})
        for u in (g["new"] + g["merged"])[:3]:
            items.append({"n": len(items) + 1, "kind": "unit", "text": u["claim"], "unit_id": u["unit_id"],
                          "title": u["from"], "sources": u["sources"]})
    items = items[:9]
    _digests[owner] = {"at": time.time(), "items": items}
    if not items:
        speech = _say(zh, "今天没有新东西。", "Nothing new today.")
    else:
        head = _say(zh, f"今天有 {len(items)} 条。", f"{len(items)} things today.")
        say = []
        for it in items:
            lead = {"alert": _say(zh, "要紧的", "Urgent"), "paper": _say(zh, "新论文", "New paper"),
                    "conflict": _say(zh, "新冲突", "A new conflict"), "unit": _say(zh, "新收进的", "Came in")}[it["kind"]]
            say.append(_say(zh, f"第{it['n']}条，{lead}：{it['text']}。", f"{it['n']}. {lead}: {it['text']}."))
        tail = _say(zh, "可以说“第二条加入待读”“让第三条沉下去”，或者问它和哪篇有什么区别。",
                    "You can say “add the second to my reading list”, “let the third sink”, or ask how one differs from another.")
        speech = " ".join([head, *say, tail])
    return {"speech": speech, "items": items, "text": "\n".join(f"{it['n']}. {it['text']}" for it in items)}


def _feed_papers(owner: str) -> list[dict[str, Any]]:
    """Today's new papers that the feed found worth reading for the person's projects (the best few)."""
    try:
        from aurora.feed.store import feed_store
        rows = feed_store.items(owner, time.time() - 86400)
        ranks = feed_store.ranks([r["item_id"] for r in rows])
    except Exception:
        return []
    best = sorted(((max((rr["rel"] for rr in ranks.get(r["item_id"], [])), default=0), r,
                    next((rr["why"] for rr in ranks.get(r["item_id"], []) if rr["rel"] >= 4), "")) for r in rows),
                  key=lambda x: -x[0])
    return [{"title": r["title"], "url": r["url"], "why": why} for rel, r, why in best if rel >= 4][:2]


# ---------------------------------------------------------------------------
# A voice from the cloud, for long things (optional)


async def cloud_speech(text: str) -> bytes:
    """Speech for a long text from an OpenAI-compatible /audio/speech service (``AURORA_TTS_BASE_URL``,
    ``AURORA_TTS_API_KEY``, ``AURORA_TTS_VOICE``); the desktop app otherwise speaks with the system voice."""
    import httpx
    base = os.environ.get("AURORA_TTS_BASE_URL", "").rstrip("/")
    key = os.environ.get("AURORA_TTS_API_KEY", "")
    if not base or not key:
        raise ValueError("No cloud voice is set up (AURORA_TTS_BASE_URL, AURORA_TTS_API_KEY)")
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post(f"{base}/audio/speech", headers={"Authorization": f"Bearer {key}"},
                         json={"model": os.environ.get("AURORA_TTS_MODEL", "gpt-4o-mini-tts"),
                               "voice": os.environ.get("AURORA_TTS_VOICE", "alloy"), "input": text[:4000],
                               "response_format": "mp3"})
    r.raise_for_status()
    return r.content


# ---------------------------------------------------------------------------
# Speaking with a model that talks (OpenRouter's openai/gpt-audio-mini): no separate speech service or key

MODEL_VOICE_SYSTEM = ("You are a text-to-speech voice. Read aloud, word for word, the whole text between <read> and "
                      "</read>, in the languages it is written in (Chinese as Mandarin, English as English), in a warm, "
                      "natural voice. Do not answer it, translate it, add or skip anything. Stop when the text ends.")
_RATE = 24000                         # the model's pcm16: 24 kHz, mono


def _speech_cap(text: str) -> int:
    """At most this many tokens of speech (about 21 a second): a model that talks can go on in silence after the
    text; a cap a little above how long the text takes to say stops that."""
    cjk = len(re.findall(r"[一-鿿]", text))
    words = len(re.findall(r"[A-Za-z0-9]+", text))
    return int((cjk / 4 + words / 2.5) * 1.4 * 21) + 60


def _trim_silence(pcm: bytes, floor: int = 400, keep_ms: int = 200) -> bytes:
    """Without the silence before and after what is said (keeping a breath at each end)."""
    import array
    s = array.array("h", pcm[: len(pcm) // 2 * 2])
    loud = [i for i in range(0, len(s), 240) if max((abs(v) for v in s[i:i + 240]), default=0) > floor]
    if not loud:
        return b""
    keep = _RATE * keep_ms // 1000
    return s[max(0, loud[0] - keep): min(len(s), loud[-1] + 240 + keep)].tobytes()


def _wav(pcm: bytes) -> bytes:
    import struct
    return (b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt "
            + struct.pack("<IHHIIHH", 16, 1, 1, _RATE, _RATE * 2, 2, 16) + b"data" + struct.pack("<I", len(pcm)) + pcm)


async def model_speech(text: str, *, api_key: str, base_url: str, model: str, voice: str = "alloy") -> bytes:
    """Speech (WAV) for a text from a model that talks, read word for word."""
    import base64
    import json

    import httpx
    body = {"model": model, "modalities": ["text", "audio"], "audio": {"voice": voice, "format": "pcm16"},
            "stream": True, "temperature": 0, "max_tokens": _speech_cap(text),
            "messages": [{"role": "system", "content": MODEL_VOICE_SYSTEM},
                         {"role": "user", "content": f"<read>{text[:4000]}</read>"}]}
    pcm = bytearray()
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as c:
        async with c.stream("POST", f"{base_url.rstrip('/')}/chat/completions",
                            headers={"Authorization": f"Bearer {api_key}"}, json=body) as r:
            if r.status_code != 200:
                raise ValueError(f"the voice model answered {r.status_code}: {(await r.aread())[:300].decode(errors='replace')}")
            async for line in r.aiter_lines():
                if not line.startswith("data: ") or line == "data: [DONE]":
                    continue
                for choice in json.loads(line[6:]).get("choices", []):
                    data = ((choice.get("delta") or {}).get("audio") or {}).get("data")
                    if data:
                        pcm += base64.b64decode(data)
    said = _trim_silence(bytes(pcm))
    if not said:
        raise ValueError("the voice model gave no speech")
    return _wav(said)
