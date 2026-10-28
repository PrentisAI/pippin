"""The paper reader: notes by a person and the agents on a PDF, private by default, and their way to Zotero."""

import json
import os
import re
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from aurora.brainstorm import agents as ag
from aurora.brainstorm.store import bs_store
from aurora.literature import indexes as indexes_mod
from aurora.literature import zotero as z
from aurora.literature.annotations import annotations, find_page
from aurora.literature.library import library
from aurora.literature.zotero import compose_comment, parse_comment, zotero_store
from tests.test_brainstorm_more import _pdf
from tests.test_literature import BEHRENS, FakeIndexes
from tests.test_zotero import FakeZotero

LINES = ["Forces are not enough: a benchmark for force fields",
         "We show that force accuracy alone does not suffice for effective simulation.",
         "We identify stability as a key metric for models to improve.",
         "Water is the cheapest system on which the rankings disagree."]
RECTS = [[72.0, 700.0, 300.5, 712.25]]


def _paper_with_pdf() -> str:
    key = library.upsert_paper(BEHRENS)["key"]
    folder = Path(os.environ["AURORA_HOME"]) / "library" / "local" / "papers"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{key}.pdf").write_bytes(_pdf(LINES))
    return key


def _reply(content: str):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=[]))])


class ReadingModel:
    """Agents that mark the passage their role cares about, and answer on a passage."""

    def __init__(self):
        self.prompts = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    async def create(self, *, model, messages, **kw):
        text = messages[0]["content"]
        self.prompts.append(messages)
        if "mark up to" in text:
            quote = {"the Librarian": LINES[1], "the Skeptic": LINES[2]}.get(
                re.search(r"You are (the \w+)", text).group(1), "A sentence the paper does not have at all, made up.")
            idea = re.search(r"- (c_\w+|chat:\w+):", text).group(1)
            return _reply(json.dumps({"marks": [{"quote": quote, "comment": "It bears on the idea.", "rel": "supports",
                                                 "idea": idea}]}))
        who = re.search(r"You are the (\w+)", text).group(1)
        return _reply(f"{who}: only water is shown on this page.")


@pytest.fixture()
def model(monkeypatch):
    fake = ReadingModel()
    monkeypatch.setattr(ag, "client_factory", lambda access: fake)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-123456")
    return fake


@pytest.fixture()
def client(model):
    from aurora.api.app import create_app
    return TestClient(create_app())


def _settle(c, key, space="global", card_id="", timeout=20.0, chat_id=""):
    """Agents answer and read in the background: wait until none is still at it."""
    end = time.time() + timeout
    while time.time() < end:
        r = c.get(f"/api/reader/papers/{key}", params={"space": space, "card_id": card_id, "chat_id": chat_id}).json()
        busy = any(x["pending"] for n in r["notes"] for x in n["replies"]) or \
            any(v["state"] == "reading" for v in r["reads"].values())
        if not busy:
            return r
        time.sleep(0.1)
    raise AssertionError("the agents did not finish")


def test_a_quote_is_found_across_lines_and_hyphens():
    pages = ["We show that force accu-\nracy alone does not\nsuffice for effective simulation.", "page two"]
    assert find_page(pages, "force accuracy alone does not suffice") == 0
    assert find_page(pages, "a sentence that is not there at all") == -1
    assert find_page(pages, "too short") == -1


def test_notes_on_a_paper_are_yours_and_undoable(client):
    key = _paper_with_pdf()
    r = client.get(f"/api/reader/papers/{key}", params={"space": "global"}).json()
    assert r["pdf"]["available"] and r["notes"] == [] and r["zotero"]["state"] == "off" and r["agents_mark"]
    assert client.get(f"/api/reader/papers/{key}/pdf").content.startswith(b"%PDF")
    n = client.post(f"/api/reader/papers/{key}/notes", json={
        "space": "global", "text": LINES[1], "comment": "The gap our idea is about.", "page_index": 0, "rects": RECTS,
        "sort_index": "00000|000051|00080"}).json()
    assert (n["status"], n["why"], n["color"], n["mine"]) == ("local", "Zotero is not connected", "#ffd400", True)
    n = client.patch(f"/api/reader/notes/{n['ann_id']}", json={"comment": "Exactly the gap."}).json()
    assert n["comment"] == "Exactly the gap."
    assert client.delete(f"/api/reader/notes/{n['ann_id']}").json() == {"deleted": True, "in_zotero": False}
    assert client.get(f"/api/reader/papers/{key}").json()["notes"] == []
    client.post(f"/api/reader/notes/{n['ann_id']}/restore")
    assert [x["comment"] for x in client.get(f"/api/reader/papers/{key}").json()["notes"]] == ["Exactly the gap."]
    # a draft (a comment being written) becomes a note when saved
    d = client.post(f"/api/reader/papers/{key}/notes", json={"text": LINES[3], "draft": True, "rects": RECTS}).json()
    assert d["status"] == "draft"
    assert client.patch(f"/api/reader/notes/{d['ann_id']}", json={"comment": "Start here."}).json()["state"] == "kept"
    # nobody else sees them; sharing needs a project
    pid = library.paper(key)["paper_id"]
    assert annotations.for_paper(pid, "someone-else") == []
    assert client.patch(f"/api/reader/notes/{d['ann_id']}", json={"shared": True}).status_code == 403
    assert client.post(f"/api/reader/papers/{key}/notes", json={"text": " "}).status_code == 400


def test_shared_notes_are_seen_in_their_project_only():
    library.upsert_paper(BEHRENS)
    pid = library.paper("Behrens12")["paper_id"]
    mine = annotations.add("u1", pid, text="a passage", space="p_1")
    annotations.update(mine["ann_id"], shared=True)
    annotations.add("u1", pid, text="private passage", space="p_1")
    assert [a["text"] for a in annotations.for_paper(pid, "u2", "p_1")] == ["a passage"]
    assert annotations.for_paper(pid, "u2", "p_2") == []
    assert len(annotations.for_paper(pid, "u1", "p_2")) == 2


def test_the_agents_read_along_and_answer_on_a_passage(client, model):
    key = _paper_with_pdf()
    chat = bs_store.new_chat("user:local", "local", "Force fields")
    idea = bs_store.add_card("user:local", "local", "idea", "Judge force fields by their simulations", by="local",
                             chat_id=chat["chat_id"])
    started = client.post(f"/api/reader/papers/{key}/read-along",
                          json={"space": "global", "card_id": idea["card_id"]}).json()
    assert started == {"started": True}
    r = _settle(client, key, card_id=idea["card_id"])
    assert {a: v["state"] for a, v in r["reads"].items()} == {"Librarian": "done", "Skeptic": "done", "Methods": "done"}
    # a quote the paper does not have is dropped; the others are suggestions, in their agent's colour
    got = {n["author"]: n for n in r["notes"]}
    assert set(got) == {"Librarian", "Skeptic"} and r["idea"]["card_id"] == idea["card_id"]
    assert got["Skeptic"]["status"] == "suggested" and got["Skeptic"]["color"] == "#f19837"
    assert got["Skeptic"]["text"] == LINES[2] and got["Skeptic"]["rects"] == []
    assert got["Librarian"]["idea_text"] == "Judge force fields by their simulations"
    # read once per paper and idea
    assert client.post(f"/api/reader/papers/{key}/read-along", json={"card_id": idea["card_id"]}).json()["started"] is False
    # placing it on the page and keeping it makes it yours
    sk = got["Skeptic"]["ann_id"]
    assert client.patch(f"/api/reader/notes/{sk}", json={"comment": "mine now"}).status_code == 400
    kept = client.patch(f"/api/reader/notes/{sk}", json={"keep": True, "place": {"page_index": 0, "rects": RECTS}}).json()
    assert (kept["state"], kept["status"], kept["rects"]) == ("kept", "local", RECTS)
    # ask about a passage: the agent answers in its conversation
    asked = client.post(f"/api/reader/papers/{key}/notes", json={
        "space": "global", "card_id": idea["card_id"], "text": LINES[3], "rects": RECTS, "ask": "Skeptic"}).json()
    assert asked["comment"] == "@Skeptic What does this mean for the idea?"
    assert asked["replies"][0]["pending"] and asked["replies"][0]["who"] == "Skeptic"
    r = _settle(client, key, card_id=idea["card_id"])
    (n,) = [n for n in r["notes"] if n["ann_id"] == asked["ann_id"]]
    assert n["replies"][0]["text"] == "Skeptic: only water is shown on this page."
    talk = model.prompts[-1][0]["content"]
    assert LINES[3] in talk and "Judge force fields" in talk
    # a reply naming another agent brings that one in
    client.post(f"/api/reader/notes/{asked['ann_id']}/replies", json={"text": "@Methods how would we test it?"})
    r = _settle(client, key, card_id=idea["card_id"])
    (n,) = [n for n in r["notes"] if n["ann_id"] == asked["ann_id"]]
    assert [x["who"] for x in n["replies"]] == ["Skeptic", "local", "Methods"]
    # agents mark passages only when asked, if you say so
    client.put("/api/reader/settings", json={"agents_mark": False})
    other = bs_store.add_card("user:local", "local", "idea", "Another idea", by="local", chat_id=chat["chat_id"])
    assert client.post(f"/api/reader/papers/{key}/read-along", json={"card_id": other["card_id"]}).json()["started"] is False


async def test_an_agent_marks_a_passage_from_the_brainstorm(model):
    key = _paper_with_pdf()
    chat = bs_store.new_chat("user:local", "local", "Force fields")
    idea = bs_store.add_card("user:local", "local", "idea", "Judge force fields by simulations", by="local",
                             chat_id=chat["chat_id"])
    board = ag.Board(space="user:local", viewer="local", viewer_name="The researcher", chat=chat, cards=[idea])
    msg = bs_store.add_message(chat["chat_id"], "Skeptic", "", pending=True)
    tools = ag.BoardTools(board, "Skeptic", ag.Turn(msg["msg_id"]))
    tools.known_papers[key] = library.paper(key)["paper_id"]
    text = await tools.call("read_paper", {"paper_key": key})
    assert "--- page 1 of 1 ---" in text and "stability" in text
    bad = await tools.call("annotate", {"paper_key": key, "quote": "words the paper never says anywhere", "comment": "x",
                                        "card_id": idea["card_id"]})
    assert "does not contain" in bad["error"]
    assert (await tools.call("annotate", {"paper_key": key, "quote": LINES[2], "comment": "Stable is not accurate.",
                                          "card_id": idea["card_id"], "rel": "challenges"}))["ok"]
    (a,) = annotations.for_paper(library.paper(key)["paper_id"], "local", "user:local")
    assert (a["author"], a["state"], a["rel"], a["card_id"]) == ("Skeptic", "suggested", "challenges", idea["card_id"])
    assert tools.turn.effects == [[f"Marked a passage of [{key}] for", idea["card_id"]]]


def test_comments_say_who_wrote_them_and_read_back():
    a = {"author": "Skeptic", "comment": "Stable is not accurate.", "idea_text": "Judge force fields by simulations"}
    text = compose_comment(a, [{"who": "local", "text": "Agreed.", "pending": False},
                               {"who": "Skeptic", "text": "", "pending": True}], {"local": "You"})
    assert text == ("Skeptic (AURORA): Stable is not accurate.\n\nIdea: Judge force fields by simulations\n\n"
                    "— You: Agreed.")
    assert parse_comment(text, "Skeptic") == "Stable is not accurate."
    assert parse_comment("Edited in Zotero\n\n— You: Agreed.", "") == "Edited in Zotero"


# -- Zotero ------------------------------------------------------------------------------------


class FakeZoteroNotes(FakeZotero):
    """The fake Zotero, with annotations: created with versions, changed, deleted, listed by version."""

    def __init__(self):
        super().__init__()
        self.trash: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path, q = request.url.path, dict(request.url.params)
        if request.headers.get("Zotero-API-Key") != "secret-zotero-key-123":
            return httpx.Response(403)
        hdr = {"Last-Modified-Version": str(self.version)}
        if path == "/users/42/items" and request.method == "GET" and q.get("itemType") == "annotation":
            since = int(q.get("since", 0))
            rows = [i for i in self.items.values() if i["data"].get("itemType") == "annotation" and i["version"] > since]
            return httpx.Response(200, json=rows, headers={**hdr, "Total-Results": str(len(rows))})
        if path == "/users/42/deleted":
            return httpx.Response(200, json={"items": self.trash}, headers=hdr)
        if path == "/users/42/items" and request.method == "POST":
            ok = {}
            for k, it in enumerate(json.loads(request.content)):
                key = f"NEW{len(self.created) + 1}"
                self.created.append(it)
                self.version += 1
                self.items[key] = {"key": key, "version": self.version, "data": {**it, "key": key}}
                ok[str(k)] = {"key": key, "version": self.version}
            return httpx.Response(200, json={"successful": ok, "failed": {}})
        m = re.fullmatch(r"/users/42/items/(\w+)", path)
        if m and request.method in ("GET", "PATCH", "DELETE"):
            it = self.items.get(m.group(1))
            if it is None:
                return httpx.Response(404)
            if request.method == "GET":
                return httpx.Response(200, json=it)
            if int(request.headers.get("If-Unmodified-Since-Version", -1)) != it["version"]:
                return httpx.Response(412)
            if request.method == "DELETE":
                self.deleted.append(m.group(1))
                del self.items[m.group(1)]
                return httpx.Response(204)
        return super().handler(request)


@pytest.fixture()
def zclient(model, monkeypatch):
    fake = FakeZoteroNotes()
    monkeypatch.setattr(indexes_mod, "_shared", FakeIndexes())
    monkeypatch.setattr(z, "http_client", httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)))
    monkeypatch.setattr(z, "notes_soon", lambda *a, **k: None)       # the test syncs when it means to
    from aurora.api.app import create_app
    c = TestClient(create_app())
    c.fake = fake
    return c


async def test_notes_go_to_zotero_and_come_back(zclient):
    c, fake = zclient, zclient.fake
    cid = c.post("/api/campaigns/quick", json={"prompt": "Methanol"}).json()["campaign_id"]
    c.post("/api/zotero/connect", json={"method": "web", "api_key": "secret-zotero-key-123"})
    zotero_store.set_maps("local", [{"library": "user", "collection_key": "COLA", "name": "Methanol", "campaign_id": cid}])
    await z.sync("local", "You")
    key = "Behrens12"
    # writing to Zotero is off until allowed: the reader says so
    assert c.get(f"/api/reader/papers/{key}", params={"space": cid}).json()["zotero"]["state"] == "writes_off"
    from aurora.auth import writes
    writes.set_levels("local", {"zotero.annotate": "ask", "zotero.save": "ask"})
    r = c.get(f"/api/reader/papers/{key}", params={"space": cid}).json()
    # the highlight made in Zotero is in the reader, as the person's note
    assert r["zotero"]["state"] == "ok" and r["pdf"]["available"]
    (zn,) = r["notes"]
    assert (zn["text"], zn["comment"], zn["source"], zn["status"]) == ("Zn decorates the Cu steps", "the key result",
                                                                       "zotero", "local")    # it has no place on the page
    # a note made here goes to the PDF in Zotero: same place, own colour, tagged
    n = c.post(f"/api/reader/papers/{key}/notes", json={"space": cid, "text": "steps", "comment": "The gap.",
                                                        "page_index": 2, "page_label": "3", "rects": RECTS}).json()
    assert n["status"] == "saving"
    await z.sync_notes("local")
    made = fake.created[-1]
    assert (made["itemType"], made["parentItem"], made["annotationColor"], made["annotationComment"]) == \
        ("annotation", "A1", "#ffd400", "The gap.")
    assert json.loads(made["annotationPosition"]) == {"pageIndex": 2, "rects": RECTS}
    assert made["annotationSortIndex"] == "00002|000000|00000" and made["tags"] == [{"tag": "aurora"}]
    assert {x["ann_id"]: x["status"] for x in c.get(f"/api/reader/papers/{key}", params={"space": cid}).json()["notes"]}[
        n["ann_id"]] == "synced"
    # a kept suggestion of the Skeptic says who wrote it
    pid = library.paper(key)["paper_id"]
    sk = annotations.add("local", pid, author="Skeptic", state="suggested", text="Cu steps", comment="Steps or interface?",
                         rects=RECTS, space=cid)
    c.patch(f"/api/reader/notes/{sk['ann_id']}", json={"keep": True})
    await z.sync_notes("local")
    assert fake.created[-1]["annotationComment"] == "Skeptic (AURORA): Steps or interface?"
    assert fake.created[-1]["annotationColor"] == "#f19837" and {"tag": "aurora:skeptic"} in fake.created[-1]["tags"]
    # a reply changes the comment in Zotero
    c.post(f"/api/reader/notes/{n['ann_id']}/replies", json={"text": "Check the materials too."})
    await z.sync_notes("local")
    zkey = annotations.get(n["ann_id"])["zotero_key"]
    assert fake.items[zkey]["data"]["annotationComment"] == "The gap.\n\n— You: Check the materials too."
    # changed in Zotero: the reader takes it
    fake.version += 1
    fake.items[zkey]["data"].update(annotationComment="Edited in Zotero\n\n— You: Check the materials too.",
                                    dateModified="2099-01-01T00:00:00Z")
    fake.items[zkey]["version"] = fake.version
    await z.sync_notes("local")
    assert annotations.get(n["ann_id"])["comment"] == "Edited in Zotero"
    # deleted here: Zotero is told once the time for Undo is over
    c.delete(f"/api/reader/notes/{n['ann_id']}")
    await z.sync_notes("local")
    assert zkey in fake.items
    annotations.update(n["ann_id"], touch=False, deleted_at=time.time() - 30)
    await z.sync_notes("local")
    assert zkey not in fake.items and annotations.get(n["ann_id"]) is None
    # deleted in Zotero: gone here too
    skey = annotations.get(sk["ann_id"])["zotero_key"]
    fake.trash.append(skey)
    del fake.items[skey]
    await z.sync_notes("local")
    assert annotations.get(sk["ann_id"]) is None


async def test_notes_stay_here_when_zotero_cannot_take_them(zclient):
    c, fake = zclient, zclient.fake
    key = _paper_with_pdf()
    from aurora.auth import writes
    writes.set_levels("local", {"zotero.annotate": "ask", "zotero.save": "ask"})
    c.post("/api/zotero/connect", json={"method": "web", "api_key": "secret-zotero-key-123"})
    n = c.post(f"/api/reader/papers/{key}/notes", json={"text": LINES[1], "rects": RECTS}).json()
    assert (n["status"], n["why"]) == ("local", "The paper is not in your Zotero")
    # no collection for your Library yet
    assert "Choose a Zotero collection" in c.post(f"/api/reader/papers/{key}/zotero", json={}).json()["detail"]
    # a read-only key: the notes stay here
    zotero_store.save_link("local", "web", "42", "linda", {"user": {"library": True, "write": False}})
    zotero_store.put_item("local", "user", "I9", paper_id=library.paper(key)["paper_id"], attachment="A9")
    r = c.get(f"/api/reader/papers/{key}").json()
    assert r["zotero"]["state"] == "read_only" and r["notes"][0]["why"] == "Your Zotero key can only read"
    await z.sync_notes("local")
    assert not any(it.get("itemType") == "annotation" for it in fake.created)


def test_an_open_pdf_is_fetched_once_with_its_progress(client, tmp_path, monkeypatch):  # noqa: F811
    """Shown a paper, the console starts fetching its PDF; opening it then waits for that download."""
    import asyncio

    import httpx

    from aurora.literature import reading
    from aurora.literature.library import library
    body = b"%PDF-1.4\n" + b"x" * 5000
    calls = []
    gate = asyncio.Event()

    async def handler(request):
        calls.append(str(request.url))
        await gate.wait()
        return httpx.Response(200, headers={"content-length": str(len(body))}, content=body)

    monkeypatch.setattr(reading, "http_client", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(reading, "cached_pdf", lambda pid: tmp_path / f"{pid}.pdf")
    entry = {"paper": type("P", (), {"arxiv_id": "2510.23981", "pdf_url": ""})()}
    monkeypatch.setattr(library, "paper", lambda pid: entry)

    async def run():
        job = reading.start_fetch("p1")
        assert reading.start_fetch("p1") is job                     # under way: not started again
        assert reading.fetch_progress("p1")["state"] == "fetching"
        waiting = asyncio.create_task(reading.fetch_open_pdf("p1"))
        await asyncio.sleep(0)
        gate.set()
        path = await waiting
        assert path is not None and path.read_bytes() == body
        assert reading.fetch_progress("p1")["state"] == "ready"

    asyncio.run(run())
    assert calls == ["https://arxiv.org/pdf/2510.23981"]


def test_a_page_that_is_not_a_pdf_is_not_kept(tmp_path, monkeypatch):
    import asyncio

    import httpx

    from aurora.literature import reading
    from aurora.literature.library import library
    monkeypatch.setattr(reading, "http_client", httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=b"<html>captcha</html>"))))
    monkeypatch.setattr(reading, "cached_pdf", lambda pid: tmp_path / f"{pid}.pdf")
    monkeypatch.setattr(library, "paper", lambda pid: {"paper": type("P", (), {"arxiv_id": "1", "pdf_url": ""})()})
    assert asyncio.run(reading.fetch_open_pdf("p2")) is None
    assert not (tmp_path / "p2.pdf").exists() and not (tmp_path / "p2.part").exists()
    assert reading.fetch_progress("p2")["state"] == "none"


def test_with_no_idea_yet_the_agents_read_for_the_conversation(client, model):
    """A Mentor's conversation writes its idea down only at the end: until then the agents read for what
    the researcher said in it."""
    key = _paper_with_pdf()
    chat = bs_store.new_chat("user:local", "local", "Memory")
    bs_store.add_message(chat["chat_id"], "local", "@Mentor I don't know yet what to research.")
    bs_store.add_message(chat["chat_id"], "Mentor", "What made you stop and wonder lately?")
    bs_store.add_message(chat["chat_id"], "local", "How stable simulations of water are with learned force fields")
    view = client.get(f"/api/reader/papers/{key}", params={"space": "global", "chat_id": chat["chat_id"]}).json()
    assert view["idea"]["kind"] == "conversation" and "@Mentor" not in view["idea"]["text"]
    assert "learned force fields" in view["idea"]["text"] and "What made you stop" not in view["idea"]["text"]
    started = client.post(f"/api/reader/papers/{key}/read-along", json={"space": "global", "chat_id": chat["chat_id"]})
    assert started.json() == {"started": True}
    r = _settle(client, key, chat_id=chat["chat_id"])
    assert {a: v["state"] for a, v in r["reads"].items()} == {"Librarian": "done", "Skeptic": "done", "Methods": "done"}
    got = {n["author"]: n for n in r["notes"]}
    assert set(got) == {"Librarian", "Skeptic"} and all(n["card_id"] == "" for n in got.values())
    assert any("learned force fields" in p[0]["content"] for p in model.prompts if "mark up to" in p[0]["content"])
    # read once for the conversation
    assert client.post(f"/api/reader/papers/{key}/read-along",
                       json={"space": "global", "chat_id": chat["chat_id"]}).json()["started"] is False


async def test_an_agent_reads_a_paper_it_has_no_pdf_of_from_its_open_copy(model, monkeypatch, tmp_path):
    """Not on this machine: the agent fetches the open copy (arXiv) once, then reads it. The Mentor may read too."""
    from aurora.literature import reading
    key = library.upsert_paper(BEHRENS)["key"]
    pid = library.paper(key)["paper_id"]
    fetched = []

    async def fetch(paper_id):
        fetched.append(paper_id)
        path = tmp_path / f"{paper_id}.pdf"
        path.write_bytes(_pdf(LINES))
        return path

    monkeypatch.setattr(reading, "fetch_open_pdf", fetch)
    chat = bs_store.new_chat("user:local", "local", "Ground truth")
    board = ag.Board(space="user:local", viewer="local", viewer_name="The researcher", chat=chat, cards=[])
    assert "read_paper" in {t["function"]["name"] for t in ag.tool_specs("Mentor", board)}
    msg = bs_store.add_message(chat["chat_id"], "Mentor", "", pending=True)
    tools = ag.BoardTools(board, "Mentor", ag.Turn(msg["msg_id"]))
    tools.known_papers[key] = pid
    text = await tools.call("read_paper", {"paper_key": key})
    assert "--- page 1 of 1 ---" in text and "stability" in text
    assert fetched == [pid] and any("Fetching the PDF" in s for s in tools.turn.steps)


def test_papers_cited_in_your_own_brainstorm_are_in_your_library(tmp_path):
    """A personal space's literature is the person's own library: what agents cite there shows on the Library page and
    in the space's Papers, and papers kept under the space before are found there too."""
    from aurora.literature.library import LibraryStore
    from aurora.literature.service import ProjectLiterature
    lib = LibraryStore(tmp_path)
    stored = lib.upsert_paper(BEHRENS)
    # kept under the space only (as before): put in the owner's library when the library opens
    lib.add_item("user:local", stored["paper_id"], source="agent", added_by="Mentor")
    lib.close()
    lib = LibraryStore(tmp_path)
    assert [i["paper_id"] for i in lib.items("", owner="local")] == [stored["paper_id"]]
    lib.close()
    # cited from now on: in both
    other = library.upsert_paper(BEHRENS)
    ProjectLiterature("user:alice")._keep(other["paper_id"], source="agent", added_by="Mentor")
    assert library.has_item("user:alice", other["paper_id"]) and library.has_item("", other["paper_id"], owner="alice")
