"""The memory at the entry points: interruptions (retractions, overlaps), the write log and undo, citations from your
own library, the reference check before submitting, your notes (an Obsidian vault), the Zotero card, and voice."""

import json
import time

import pytest

from aurora.literature.library import library
from aurora.literature.records import Paper
from aurora.memory import alerts, surface, voice
from aurora.memory.store import mem_store
from tests.test_brainstorm import _reply
from tests.test_memory import COLLAPSE, EN, ZH, FakeModel, save
from tests.test_memory import client as client  # noqa: F401  (the fixture)


class EntryModel(FakeModel):
    """The memory's fake model, plus what the entry points ask of it."""

    async def create(self, *, model, messages, **kw):
        prompt = messages[-1]["content"]
        task = prompt.split("\n", 1)[0].removeprefix("TASK: ")
        if task == "memory_cite":
            ids = [line.split("]")[0].strip(" [") for line in prompt.splitlines() if line.startswith("[")]
            self.tasks.append(task)
            return _reply(json.dumps({"cites": [{"id": ids[0], "quote": "speedup vanishes at batch size 1", "how": "states it"},
                                                {"id": ids[0], "quote": "words it never says", "how": "made up"}]}))
        if task == "memory_miscite":
            self.tasks.append(task)
            return _reply(json.dumps({"items": [{"n": 1, "verdict": "no", "issue": "The paper finds 1.2x, not 3x.",
                                                 "quote": "a 1.2x speedup"}]}))
        if task == "memory_voice":
            self.tasks.append(task)
            said = prompt.split('They said: "', 1)[1].split('"', 1)[0]
            if "待读" in said:
                return _reply(json.dumps({"intent": "toread", "targets": ["this"], "lang": "zh"}))
            if "撤销" in said:
                return _reply(json.dumps({"intent": "undo", "targets": [], "lang": "zh"}))
            if "digest" in said:
                return _reply(json.dumps({"intent": "digest", "targets": [], "lang": "en"}))
            if "sink" in said:
                return _reply(json.dumps({"intent": "sink", "targets": ["digest:1"], "lang": "en"}))
            if "note" in said:
                return _reply(json.dumps({"intent": "note", "targets": ["this"], "note": "batch size matters more than I thought",
                                          "lang": "en"}))
            return _reply(json.dumps({"intent": "ask", "targets": ["this"], "question": "Does the speedup hold at batch 1?",
                                      "lang": "en"}))
        if task == "memory_voice_answer":
            self.tasks.append(task)
            ids = [line.split("]")[0].strip(" [") for line in prompt.splitlines() if line.startswith("[u_")]
            return _reply(json.dumps({"speech": "Not at batch 1, by your saves.", "answer": "It vanishes at batch 1 [u].",
                                      "units": ids[:1]}))
        return await super().create(model=model, messages=messages, **kw)


@pytest.fixture()
def model(monkeypatch):
    from aurora.brainstorm import agents as ag
    from aurora.review import llm
    fake = EntryModel()
    monkeypatch.setattr(ag, "client_factory", lambda access: fake)
    monkeypatch.setattr(llm, "client_factory", lambda access: fake)
    monkeypatch.setattr(llm, "RETRY_WAIT", 0)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-123456")
    for d in (surface._contexts, surface._history, voice._digests, voice._undo, voice._pending, voice._turns):
        d.clear()
    return fake


def _paper(title="Sparse attention at scale", doi="10.1/xyz", abstract=""):
    return library.upsert_paper(Paper(title=title, authors=["Ada Lovelace"], year=2024, doi=doi, abstract=abstract))


# ---------------------------------------------------------------------------
# Interruptions


def test_overlaps_interrupt_at_most_twice_a_day_and_not_worth_it_demotes_them(client):
    a1 = alerts.raise_alert("local", "overlap", "p1", "New work overlaps Q1", "…")
    a2 = alerts.raise_alert("local", "overlap", "p2", "New work overlaps Q1", "…")
    a3 = alerts.raise_alert("local", "overlap", "p3", "New work overlaps Q2", "…")
    assert [a["tier"] for a in (a1, a2, a3)] == ["now", "now", "digest"]
    assert alerts.raise_alert("local", "overlap", "p1", "again", "…") is None        # once per thing
    r = alerts.raise_alert("local", "retraction", "10.1/r", "Retracted", "…")
    assert r["tier"] == "now"                                                        # retractions always interrupt
    listed = client.get("/api/memory/alerts?unseen=true").json()
    assert {a["key"] for a in listed} >= {"p1", "p2", "p3", "10.1/r"}
    client.post(f"/api/memory/alerts/{a1['alert_id']}/verdict", json={"worth": False})
    mem_store._x("UPDATE mem_alerts SET created_at=? WHERE kind='overlap'", (time.time() - 2 * 86400,))
    assert alerts.raise_alert("local", "overlap", "p4", "…", "…")["tier"] == "digest"   # demoted from now on
    seen = client.post(f"/api/memory/alerts/{a2['alert_id']}/seen").json()
    assert seen["seen_at"]


def test_a_retracted_paper_you_rely_on_raises_one_alert(client, monkeypatch):
    p = _paper()
    library.add_item("", p["paper_id"], source="person", owner="local")
    calls = []

    async def fake(dois):
        calls.append(dois)
        return {"10.1/xyz": "OpenAlex marks it retracted"}
    monkeypatch.setattr(alerts, "is_retracted", fake)
    r = client.post("/api/memory/alerts/check").json()
    assert r["retracted"] and "your Library" in r["retracted"][0]["body"]
    assert client.post("/api/memory/alerts/check").json()["retracted"] == []         # not twice
    assert calls == [["10.1/xyz"], ["10.1/xyz"]]


# ---------------------------------------------------------------------------
# What Pippin may write, the log, and undo


def test_entry_points_log_their_writes_only_when_allowed_and_say_how_to_undo(client):
    body = {"write": "overleaf.cite", "detail": {"key": "a"}, "undo": {"kind": "overleaf", "how": "⌘Z in Overleaf"}}
    assert client.post("/api/me/writes/log", json=body).status_code == 403              # off by default
    client.put("/api/me/writes", json={"levels": {"overleaf.cite": "auto"}})
    assert client.post("/api/me/writes/log", json=body).status_code == 200
    log = client.get("/api/me/writes").json()["log"]
    assert log[0]["write"] == "overleaf.cite"
    u = client.post(f"/api/me/writes/{log[0]['log_id']}/undo").json()
    assert u == {"undone": False, "where": "overleaf", "how": "⌘Z in Overleaf"}


# ---------------------------------------------------------------------------
# A citation for the sentence you are writing; the references before you submit


def test_citations_come_from_your_library_with_the_passage_that_backs_them(client):
    p = _paper(abstract="We find the sparse attention speedup vanishes at batch size 1 on a single GPU.")
    library.add_item("", p["paper_id"], source="person", owner="local")
    r = client.post("/api/memory/cite", json={"sentence": "Sparse attention does not help at batch size 1."}).json()
    (c,) = r["candidates"]                     # the made-up passage is dropped
    assert c["paper_id"] == p["paper_id"] and "@" in c["bibtex"] and c["from"] == "your Library"


def test_the_reference_check_reports_retractions_miscitations_and_undefined_keys(client, monkeypatch):
    from aurora.literature import check as check_mod
    from aurora.memory import audit

    p = _paper(abstract="Sparse attention gives a 1.2x speedup at long context.")

    async def fake_check(ref):
        from types import SimpleNamespace
        ok = "Sparse" in (ref.title or "")
        return SimpleNamespace(verified=ok, status="verified" if ok else "not_found", reason="" if ok else "no match",
                               paper=p["paper"] if ok else None)

    async def fake_retracted(dois):
        return {"10.1/xyz": "Retraction notice 10.1/r"}
    monkeypatch.setattr(check_mod, "check", fake_check)
    monkeypatch.setattr(alerts, "is_retracted", fake_retracted)
    tex = (r"\title{Ours}\begin{abstract}short\end{abstract} Sparse attention gives a 3x speedup \cite{lov24}. "
           r"Others agree \citep{ghost}.")
    bib = "@article{lov24, title={Sparse attention at scale}, author={Lovelace, Ada}, year={2024}, doi={10.1/xyz}}\n" \
          "@article{unused1, title={Nothing real}, author={No, Body}, year={2020}}"
    a = client.post("/api/memory/audit", json={"tex": tex, "bib": bib}).json()
    end = time.time() + 20
    while time.time() < end and (a := client.get(f"/api/memory/audits/{a['audit_id']}").json())["state"] == "running":
        time.sleep(0.05)
    rep = a["report"]
    assert a["state"] == "done", a
    assert rep["counts"]["retracted"] == 1 and rep["undefined"] == ["ghost"] and rep["unused"] == ["unused1"]
    (m,) = rep["miscites"]
    assert m["key"] == "lov24" and "1.2x" in m["issue"] and m["quote"] == "a 1.2x speedup"
    # what a manuscript cites is watched for retractions from now on
    assert "10.1/xyz" in alerts.relied_on("local")
    assert audit.citations(r"A \citet[p.~3]{a, b} B")[0]["keys"] == ["a", "b"]


# ---------------------------------------------------------------------------
# Your notes


def test_vault_notes_are_read_changed_notes_replace_their_old_version_and_deleted_ones_go(client, tmp_path):
    root = tmp_path / "Research"
    (root / "Papers").mkdir(parents=True)
    (root / "Journal").mkdir()
    (root / ".obsidian").mkdir()
    note = root / "Papers" / "sparse.md"
    note.write_text("---\ntags: [x]\n---\n" + EN + " " * 10 + "\n\nMy view: this matters for serving." * 5)
    (root / "Journal" / "today.md").write_text(COLLAPSE * 5)
    (root / "Papers" / "tiny.md").write_text("too short")
    # pointing the console at a folder on its own disks
    assert client.put("/api/memory/vault", json={"path": str(root / "nope"), "on": True}).status_code == 400
    client.put("/api/memory/vault", json={"path": str(root), "exclude": ["Journal"], "on": True})
    r = client.post("/api/memory/vault/scan").json()
    assert r == {"read": 1, "removed": 0}
    notes = {n["path"]: n for n in client.get("/api/memory/notes").json()}
    assert set(notes) == {"Papers/sparse.md", "Papers/tiny.md"} and notes["Papers/tiny.md"]["source_id"] == ""
    src = _wait(notes["Papers/sparse.md"]["source_id"])
    assert src["platform"] == "note" and src["via"] == "obsidian" and src["url"].startswith("obsidian://open?vault=Research")
    assert client.post("/api/memory/vault/scan").json()["read"] == 0                    # unchanged: left alone
    # an edit: the old version's evidence goes, the new one is read
    note.write_text(ZH * 4)
    import os
    os.utime(note, (time.time() + 5, time.time() + 5))
    assert client.post("/api/memory/vault/scan").json()["read"] == 1
    assert mem_store.source(src["source_id"])["state"] == "retired"
    note.unlink()
    assert client.post("/api/memory/vault/scan").json()["removed"] == 1


def test_the_plugin_sends_notes_and_a_unit_copies_as_markdown(client):
    r = client.post("/api/memory/notes", json={"vault": "V", "path": "a.md", "text": EN * 3}).json()
    assert r["state"] == "read"
    assert client.post("/api/memory/notes", json={"vault": "V", "path": "a.md", "text": EN * 3}).json()["state"] == "unchanged"
    s = _wait(r["source_id"])
    uid = s["result"]["units"][0]
    md = client.get(f"/api/memory/units/{uid}/markdown").json()["markdown"]
    assert md.startswith("## ") and "> " in md and f"aurora:{uid}" in md
    assert client.request("DELETE", "/api/memory/notes?vault_name=V&path=a.md").json() == {"removed": True}


def _wait(source_id):
    end = time.time() + 20
    while time.time() < end:
        s = mem_store.source(source_id)
        if s and s["state"] not in ("pending",):
            return s
        time.sleep(0.05)
    raise AssertionError("not read")


# ---------------------------------------------------------------------------
# Voice


def test_voice_resolves_this_to_what_is_in_front_and_undo_takes_it_back(client):
    p = _paper()
    client.post("/api/memory/context", json={"app": "Chrome", "title": "Sparse attention at scale",
                                             "url": "https://doi.org/10.1/xyz", "paper_id": p["paper_id"]})
    t = client.post("/api/memory/voice", json={"text": "把这篇加入待读"}).json()
    assert t["intent"] == "toread" and "待读" in t["speech"] and t["undo_id"]
    assert library.has_item("", p["paper_id"], owner="local")
    t2 = client.post("/api/memory/voice", json={"text": "撤销"}).json()
    assert t2["intent"] == "undo" and not library.has_item("", p["paper_id"], owner="local")
    turns = client.get("/api/memory/voice/turns?limit=2").json()
    assert [x["said"] for x in turns] == ["撤销", "把这篇加入待读"]


def test_voice_answers_short_aloud_and_in_full_with_sources_on_the_panel(client):
    save(client, url="https://x.com/someone/status/1", text=EN)
    client.post("/api/memory/context", json={"app": "Zotero", "title": "A paper", "selection": "sparse attention speedup at batch 1"})
    t = client.post("/api/memory/voice", json={"text": "does this hold at batch one?"}).json()
    assert t["speech"] == "Not at batch 1, by your saves." and t["text"]
    assert t["items"] and t["items"][0]["sources"][0]["quote"]


def test_voice_notes_stay_in_aurora_unless_zotero_notes_are_allowed_and_the_digest_is_numbered(client):
    t = client.post("/api/memory/voice", json={"text": "note that batch size matters"}).json()
    assert t["intent"] == "note" and t["undo_id"]
    s = mem_store.sources("local", 5)[0]
    assert s["platform"] == "note" and s["via"] == "voice"
    save(client, url="https://x.com/someone/status/2", text=EN)
    d = client.get("/api/memory/voice/digest").json()
    assert d["items"] and d["items"][0]["n"] == 1 and "1." in d["speech"]
    unit = next(it for it in d["items"] if it.get("unit_id"))
    voice._digests["local"]["items"] = [{**unit, "n": 1}]
    t = client.post("/api/memory/voice", json={"text": "let the first one sink"}).json()
    assert t["intent"] == "sink" and mem_store.unit(unit["unit_id"])["sunk"]
    client.post(f"/api/memory/voice/undo/{t['undo_id']}")
    assert not mem_store.unit(unit["unit_id"])["sunk"]


def test_audio_needs_a_recogniser(client, monkeypatch):
    monkeypatch.delenv("AURORA_WHISPER", raising=False)
    r = client.post("/api/memory/voice", json={"audio": "UklGRg=="})
    assert r.status_code == 400 and "AURORA_WHISPER" in r.json()["detail"]
