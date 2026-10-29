"""The notebook: a page for every run, written from the run (the idea it took, its hypothesis, why, what it expected
against what it found); clips from papers with your own words; notes as long as you like."""

import json
import os
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from aurora.notebook import entry as nb_entry
from aurora.notebook.store import nb_store


def _run(tmp_path, *, expected=True):
    d = tmp_path / "run"
    r0 = d / "phases" / "ideation" / "round_0"
    r0.mkdir(parents=True)
    full = {"title": "Footprint-robust atlas", "description": "Profiles on a balanced cohort of stations.",
            "hypothesis": "Seasonal profiles are the same on the balanced cohort.", "falsification_check": "r below 0.95",
            "research_questions": "['Raw trends conflate use with network growth.']",
            "expected_outcomes": "['A seasonal profile figure', 'Correlation above 0.95']" if expected else "",
            "alternatives_considered": "(1) weather first", "decision": "{'rationale': 'Answers every question and adds a new angle.'}",
            "score": 5.6, "selected": False}
    other = {"title": "Weather elasticity", "hypothesis": "Rain cuts trips.", "expected_outcomes": "['x']", "score": None, "selected": False}
    said = {"title": "I think the latest proposal is fine", "description": "I think the latest proposal is fine", "selected": True}
    for i, (name, p) in enumerate((("prop_a", other), ("prop_b", full), ("prop_c", said))):
        f = r0 / f"{name}.json"
        f.write_text(json.dumps(p))
        os.utime(f, (time.time() + i, time.time() + i))
    (r0 / "meeting_state.finished.json").write_text("{}")
    (d / "phases" / "screening" / "round_0").mkdir(parents=True)
    (d / "phases" / "screening" / "round_0" / "score.json").write_text(json.dumps({"novelty": 4, "feasibility": 8}))
    (d / "artifacts.json").write_text(json.dumps({"claims": [{"statement": "r = 0.97 across years", "status": "supported"}]}))
    return SimpleNamespace(campaign_dir=d, run_id="r1", task_name="atlas", status="completed", phase="done")


def test_a_run_page_is_written_from_the_run(tmp_path):
    e = nb_entry.entry(_run(tmp_path))
    # the researcher's "the latest is fine" is not the idea: the screened proposal it points at is
    assert e["idea"]["title"] == "Footprint-robust atlas" and e["you_said"] == "I think the latest proposal is fine"
    assert e["hypothesis"].startswith("Seasonal") and e["refuted_if"] == "r below 0.95"
    assert e["why"]["gap"] == ["Raw trends conflate use with network growth."]
    assert e["why"]["rationale"].startswith("Answers") and e["why"]["scores"] == {"novelty": 4, "feasibility": 8}
    assert e["expected"] == ["A seasonal profile figure", "Correlation above 0.95"]
    assert e["claims"][0]["status"] == "supported" and nb_entry.judgeable(e)
    k = nb_entry.match_key(e)
    e["claims"].append({"statement": "peak in August", "status": "open", "confidence": None})
    assert nb_entry.match_key(e) != k                     # a new finding: judged again


async def test_expected_is_set_against_actual(tmp_path):
    e = nb_entry.entry(_run(tmp_path))
    from aurora.review import llm
    seen = {}

    async def ask(access, task, body, **kw):
        seen["body"] = body
        return {"items": [{"n": 2, "actual": "r = 0.97", "verdict": "held"}, {"n": 1, "actual": "made", "verdict": "bogus"}],
                "hypothesis": {"verdict": "held", "why": "r above 0.95"}}
    old = llm.ask
    llm.ask = ask
    try:
        got = await nb_entry.judge(SimpleNamespace(with_model=lambda m: None), {"memory": "m"}, e)
    finally:
        llm.ask = old
    assert "E2. Correlation above 0.95" in seen["body"] and "r = 0.97 across years" in seen["body"]
    assert [i["verdict"] for i in got["items"]] == ["untested", "held"] and got["hypothesis"]["verdict"] == "held"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from aurora.api.app import create_app
    from aurora.api.routes import memory, run_results
    from aurora.memory import ingest
    run = _run(tmp_path)
    monkeypatch.setattr(run_results, "_run", lambda cid, rid: run)
    monkeypatch.setattr(memory, "_access", lambda: (SimpleNamespace(), {"memory": "m"}))
    saved = []
    monkeypatch.setattr(ingest, "start", lambda access, models, owner, **what: saved.append(what) or {"source_id": "s"})
    with TestClient(create_app()) as c:
        c.saved = saved
        yield c


def test_the_run_page_is_compared_once_and_keeps_your_notes(client, monkeypatch):
    calls = []

    async def judge(access, models, e):
        calls.append(e["run_id"])
        return {"items": [{"expected": x, "actual": "?", "verdict": "held"} for x in e["expected"]], "hypothesis": {"verdict": "held", "why": ""}}
    monkeypatch.setattr(nb_entry, "judge", judge)
    p = client.get("/api/notebook/runs/p_1/r1").json()
    assert p["idea"]["title"] == "Footprint-robust atlas" and p["note"] is None and p["match"]["state"] == "judging"
    time.sleep(0.2)
    p = client.get("/api/notebook/runs/p_1/r1").json()
    assert p["match"]["state"] == "done" and p["match"]["items"][0]["verdict"] == "held" and not p["match"]["stale"]
    client.get("/api/notebook/runs/p_1/r1")
    assert calls == ["r1"]                                   # the same findings: not judged again
    r = client.put("/api/notebook/runs/p_1/r1/note", json={"html": "<p>Try 2024 only</p>", "text": "Try 2024 only"}).json()
    assert r["html"] == "<p>Try 2024 only</p>"
    assert client.get("/api/notebook/runs/p_1/r1").json()["note"]["html"] == "<p>Try 2024 only</p>"
    # a run's notes are not among the free notes
    assert client.get("/api/notebook").json()["notes"] == []


def test_clips_keep_the_words_and_go_into_the_claims(client):
    assert client.post("/api/notebook/clips", json={"paper_id": "p_x", "quote": "  "}).status_code == 400
    c = client.post("/api/notebook/clips", json={"paper_id": "p_x", "paper_title": "Trip chains", "quote": "Segregation is measured on activity spaces.",
                                                 "page_index": 3, "rects": [[1, 2, 3, 4]], "words": "Count time, not stations."}).json()
    assert c["page_index"] == 3 and c["rects"] == [[1, 2, 3, 4]] and c["words"] == "Count time, not stations."
    assert client.saved and "My note: Count time, not stations." in client.saved[0]["text"] and client.saved[0]["via"] == "clip"
    assert [x["clip_id"] for x in client.get("/api/notebook/clips?paper=p_x").json()] == [c["clip_id"]]
    assert client.get("/api/notebook/clips?paper=other").json() == []
    ch = client.patch(f"/api/notebook/clips/{c['clip_id']}", json={"words": "Our run 2 should do this.", "run_id": "r1"}).json()
    assert ch["words"] == "Our run 2 should do this." and ch["run_id"] == "r1"
    assert client.get("/api/notebook").json()["clips"] == 1
    # another person's clip is not yours to change
    nb_store.add_clip("someone", paper_id="p_y", quote="theirs")
    theirs = nb_store.clips("someone")[0]["clip_id"]
    assert client.patch(f"/api/notebook/clips/{theirs}", json={"words": "x"}).status_code == 404
    assert client.delete(f"/api/notebook/clips/{c['clip_id']}").status_code == 204
    assert client.get("/api/notebook/clips").json() == []


def test_notes_are_written_and_kept(client):
    n = client.post("/api/notebook/notes", json={}).json()
    client.put(f"/api/notebook/notes/{n['note_id']}", json={"title": "Activity spaces", "html": "<h2>Why</h2><p>Long text</p>", "text": "Why\nLong text"})
    got = client.get(f"/api/notebook/notes/{n['note_id']}").json()
    assert got["title"] == "Activity spaces" and got["html"].startswith("<h2>")
    assert client.get("/api/notebook").json()["notes"][0]["preview"] == "Why\nLong text"
    assert client.delete(f"/api/notebook/notes/{n['note_id']}").status_code == 204
    assert client.get(f"/api/notebook/notes/{n['note_id']}").status_code == 404
