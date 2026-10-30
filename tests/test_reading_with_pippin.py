"""Reading with Pippin: its notes in a paper's margin (worth thinking about, set against your work), a figure it
draws by itself where trying beats reading, gaps opened in the page to write in, pieces of papers in a note, and the
brainstorm's agents drawing figures of their own."""

import asyncio
import json
import sqlite3
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from aurora.brainstorm import agents as ag
from aurora.brainstorm.store import bs_store
from aurora.literature import companion
from aurora.literature.annotations import AnnotationStore, annotations
from aurora.literature.library import library
from tests.test_brainstorm_more import project
from tests.test_reader import LINES, RECTS, _paper_with_pdf, _reply

FIGURE = "<!DOCTYPE html><html><body><h1>Frames</h1><svg width='100' height='40'></svg><script>1</script></body></html>"


class PippinModel:
    """Pippin's notes (one mended quote, one made up, one with a figure), and the figures it is asked for."""

    def __init__(self, notes=None):
        self.prompts = []
        self.notes = notes
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    async def create(self, *, model, messages, **kw):
        text = messages[-1]["content"] if isinstance(messages[-1]["content"], str) else ""
        self.prompts.append(text)
        if text.startswith("You are Pippin, reading a paper"):
            return _reply(json.dumps({"notes": self.notes if self.notes is not None else [
                {"quote": LINES[1], "kind": "challenge", "note": "Accuracy of what, measured how?",
                 "figure": "A slider for force error; the simulation's stability falls as it grows."},
                {"quote": "Water is the cheapest system on which the rankings disagree so strongly.", "kind": "question",
                 "note": "Which stability would your runs measure?", "figure": "A second figure: never drawn."},
                {"quote": "A passage this paper does not have anywhere at all.", "kind": "connect", "note": "Lost."},
            ]}))
        if text.startswith("Write a small interactive figure"):
            return _reply(f"```html\n{FIGURE}\n```")
        return _reply("Fine.")


@pytest.fixture()
def model(monkeypatch):
    fake = PippinModel()
    monkeypatch.setattr(ag, "client_factory", lambda access: fake)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-123456")
    return fake


@pytest.fixture()
def client(model):
    from aurora.api.app import create_app
    return TestClient(create_app())


def _read(c, key, timeout=20.0):
    end = time.time() + timeout
    while time.time() < end:
        r = c.get(f"/api/reader/papers/{key}", params={"space": "global"}).json()
        if r["companion"] and r["companion"]["state"] != "reading":
            return r
        time.sleep(0.1)
    raise AssertionError("Pippin did not finish reading")


def test_pippin_reads_against_your_work_once_and_draws_one_figure(client, model):
    key = _paper_with_pdf()
    assert client.post(f"/api/reader/papers/{key}/companion", json={"space": "global"}).json() == {"started": True}
    r = _read(client, key)
    assert r["companion"] == {"state": "done", "detail": "2 notes"}
    notes = {n["kind"]: n for n in r["notes"]}
    # the made-up quote is dropped; the mended one is anchored on the words the paper has
    assert set(notes) == {"challenge", "question"}
    assert notes["question"]["text"] == "Water is the cheapest system on which the rankings disagree"
    ch = notes["challenge"]
    assert (ch["author"], ch["state"], ch["rel"], ch["inline"], ch["figure"]) == ("Pippin", "suggested", "challenges", True, True)
    assert notes["question"]["figure"] is False and notes["question"]["inline"] is False
    assert sum(p.startswith("Write a small interactive figure") for p in model.prompts) == 1
    # the figure is a page that may run only its own script, with the line that reports its height
    f = client.get(f"/api/reader/notes/{ch['ann_id']}/figure")
    assert f.status_code == 200 and f.text.startswith(FIGURE) and "figureHeight" in f.text
    assert "sandbox allow-scripts" in f.headers["content-security-policy"]
    assert client.get(f"/api/reader/notes/{notes['question']['ann_id']}/figure").status_code == 404
    # once per paper and space
    assert client.post(f"/api/reader/papers/{key}/companion", json={"space": "global"}).json()["started"] is False
    # a note of Pippin's is answered by Pippin
    client.patch(f"/api/reader/notes/{ch['ann_id']}", json={"keep": True})
    client.post(f"/api/reader/notes/{ch['ann_id']}/replies", json={"text": "Why does it matter?"})
    end = time.time() + 10
    while time.time() < end:
        reps = client.get(f"/api/reader/papers/{key}").json()["notes"]
        reps = next(n for n in reps if n["ann_id"] == ch["ann_id"])["replies"]
        if len(reps) == 2 and not reps[1]["pending"]:
            break
        time.sleep(0.1)
    assert reps[1]["who"] == "Pippin" and reps[1]["text"] == "Fine."


def test_pippin_reads_only_when_the_agents_may_mark(client, model):
    key = _paper_with_pdf()
    client.put("/api/reader/settings", json={"agents_mark": False})
    assert client.post(f"/api/reader/papers/{key}/companion", json={"space": "global"}).json()["started"] is False


def test_what_pippin_knows_of_your_work(client):
    cid = project(client, "Rank force fields by simulation")
    from aurora.notebook.store import nb_store
    nb_store.add_clip("local", paper_id="p_x", quote="Water disagrees.", paper_title="Water paper", words="Start from water")
    ctx = companion.context_of("local", cid)
    assert "Rank force fields by simulation" in ctx and "Water disagrees." in ctx and "Start from water" in ctx
    assert companion.context_of("someone", "").startswith("Nothing written down yet")


def test_a_cut_off_answer_keeps_the_notes_that_came_through_whole():
    whole = {"quote": LINES[1], "kind": "question", "note": "Which?"}
    text = '{"notes": [' + json.dumps(whole) + ', {"quote": "We identify stab'
    assert companion._notes_of(text) == [whole]
    assert companion.clean_figure("Here it is:\n```html\n<div>x</div>\n```") == "<div>x</div>"
    assert companion.clean_figure("No figure, sorry.") == ""


def test_a_gap_opened_in_the_page_is_a_note_written_there(client):
    key = _paper_with_pdf()
    n = client.post(f"/api/reader/papers/{key}/notes", json={"space": "global", "text": LINES[1], "rects": RECTS,
                                                               "draft": True, "inline": True}).json()
    assert n["inline"] and n["state"] == "draft" and not n["figure"]
    long = "First thought.\n\nA second paragraph, at length. " * 20
    n = client.patch(f"/api/reader/notes/{n['ann_id']}", json={"comment": long}).json()
    assert n["state"] == "kept" and n["inline"] and n["comment"] == long.strip()


def test_older_notes_get_the_new_columns(tmp_path):
    db = sqlite3.connect(tmp_path / "aurora.db")
    db.execute("CREATE TABLE lit_annotations (ann_id TEXT PRIMARY KEY, owner TEXT NOT NULL, paper_id TEXT NOT NULL, "
               "author TEXT NOT NULL DEFAULT '', state TEXT NOT NULL DEFAULT 'kept', text TEXT NOT NULL DEFAULT '', "
               "comment TEXT NOT NULL DEFAULT '', color TEXT NOT NULL DEFAULT '', page_index INTEGER NOT NULL DEFAULT 0, "
               "page_label TEXT NOT NULL DEFAULT '', rects TEXT NOT NULL DEFAULT '[]', sort_index TEXT NOT NULL DEFAULT '', "
               "space TEXT NOT NULL DEFAULT '', shared INTEGER NOT NULL DEFAULT 0, card_id TEXT NOT NULL DEFAULT '', "
               "rel TEXT NOT NULL DEFAULT '', chat_id TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT 'aurora', "
               "zotero_lib TEXT NOT NULL DEFAULT '', zotero_key TEXT NOT NULL DEFAULT '', zotero_version INTEGER NOT NULL DEFAULT 0, "
               "dirty INTEGER NOT NULL DEFAULT 0, sync_error TEXT NOT NULL DEFAULT '', deleted_at REAL, "
               "created_at REAL NOT NULL, updated_at REAL NOT NULL)")
    db.execute("INSERT INTO lit_annotations (ann_id, owner, paper_id, created_at, updated_at) VALUES ('a_old', 'u', 'p', 1, 1)")
    db.commit(); db.close()
    store = AnnotationStore(tmp_path)
    old = store.get("a_old")
    assert (old["inline"], old["kind"], old["figure"]) == (False, "", "")
    store.close()


def test_a_clip_goes_into_a_note_as_a_piece_of_its_page(client):
    clip = {"paper_id": "p_1", "paper_title": "Forces <are> not enough", "quote": 'A "quoted" passage & more',
            "page_index": 2, "rects": RECTS, "words": "Compare with water."}
    a = client.post("/api/notebook/clips", json={**clip, "note_id": "new"}).json()
    note = client.get(f"/api/notebook/notes/{a['note_id']}").json()
    assert note["title"] == "From Forces <are> not enough"
    assert note["html"].count("data-piece") == 1 and 'data-quote="A &quot;quoted&quot; passage &amp; more"' in note["html"]
    assert 'data-words="Compare with water."' in note["html"] and 'data-page="2"' in note["html"]
    b = client.post("/api/notebook/clips", json={**clip, "quote": "Another passage.", "note_id": a["note_id"]}).json()
    assert b["note_id"] == a["note_id"]
    assert client.get(f"/api/notebook/notes/{a['note_id']}").json()["html"].count("data-piece") == 2
    assert client.post("/api/notebook/clips", json={**clip, "note_id": "n_nope"}).status_code == 404


def test_a_brainstorm_agent_draws_a_figure_when_it_decides_to(client, model):
    chat = bs_store.new_chat("user:local", "local", "Force fields")
    board = ag.Board(space="user:local", viewer="local", viewer_name="You", chat=chat, cards=[])
    turn_msg = bs_store.add_message(chat["chat_id"], "Methods", "", pending=True)
    tools = ag.BoardTools(board, "Methods", ag.Turn(turn_msg["msg_id"]))
    tools.access = ag.ModelAccess(api_key="k", base_url="", model="m")

    async def go():
        r = await tools.call("show_figure", {"about": "Error and stability", "brief": "A slider for the force error."})
        again = await tools.call("show_figure", {"about": "Again", "brief": "Another."})
        await asyncio.gather(*ag._background)
        return r, again
    r, again = asyncio.run(go())
    assert r["ok"] and again == {"error": "one figure per answer"}
    w = [m for m in bs_store.messages(chat["chat_id"]) if m.get("widget")][-1]["widget"]
    assert w["type"] == "figure" and w["state"] == "ready" and w["about"] == "Error and stability"
    f = client.get(f"/api/brainstorm/figures/{w['fig_id']}")
    assert f.status_code == 200 and f.text.startswith(FIGURE) and "sandbox" in f.headers["content-security-policy"]
    assert "show_figure" in {t["function"]["name"] for t in ag.tool_specs("Mentor", board)}
