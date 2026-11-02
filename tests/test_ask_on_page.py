"""Asking Pippin about the page open in the browser: the side panel sends the page (as the browser shows it, signed-in
pages too) with the selection; a paper's page is read from its PDF; an answer can be kept as a note on the page."""

import pytest
from fastapi.testclient import TestClient

from aurora.literature.annotations import annotations
from aurora.literature.library import library
from aurora.literature.records import Paper
from aurora.memory import assistant, onpage
from test_assistant import ACCESS, Scripted

URL = "https://example.org/posts/agents/"
FILLER = "Planning breaks a task into steps and the agent follows them one by one. " * 120
REFLECT = "Reflection lets the agent look back at its own failed attempts and write down what to change next time."
TEXT = f"{FILLER}\n\n{REFLECT}\n\n{FILLER}"


@pytest.fixture()
def model(monkeypatch):
    from aurora.human.frontdesk import FrontDeskAgent
    holder = {}
    monkeypatch.setattr(FrontDeskAgent, "_get_client", lambda self: holder["m"])
    assistant._agents.clear()
    return holder


@pytest.fixture()
def client(monkeypatch, model):
    from aurora.api.app import create_app
    from aurora.api.routes import assistant as route
    monkeypatch.setattr(route, "_access", lambda: (ACCESS, None, {}))
    with TestClient(create_app()) as c:
        yield c


def test_a_question_about_the_page_reads_the_passages_that_bear_on_it(client, model):
    model["m"] = Scripted([[("this_page", {"question": "what does it say about reflection on failures"})],
                           "It says reflection writes down what to change next time (part 3)."])
    page = {"url": URL + "#memory", "title": "LLM Powered Agents", "text": TEXT, "selection": ""}
    r = client.post("/api/assistant", json={"text": "what about reflection?", "page": page}).json()
    assert r["page"] == {"url": URL, "title": "LLM Powered Agents", "selection": ""}
    told = model["m"].sent[0][-1]["content"]
    assert '[They are looking at: the page open in their browser, "LLM Powered Agents"' in told and "this_page" in told
    tool = model["m"].sent[1][-1]["content"]
    assert REFLECT in tool and "the page as the browser shows it" in tool
    assert "Planning breaks" in tool or len(tool) < len(TEXT)       # passages, not the whole text
    turns = client.get("/api/assistant/history").json()
    assert turns[0]["page"]["url"] == URL and turns[1]["page"]["url"] == URL


def test_an_answer_is_kept_as_a_note_on_the_page_it_was_about(client, model):
    model["m"] = Scripted([[("this_page", {"question": "reflection"})], "It learns from failed attempts."])
    page = {"url": URL, "title": "LLM Powered Agents", "text": TEXT, "selection": "look back at its own failed attempts"}
    turn = client.post("/api/assistant", json={"text": "What does this mean?", "page": page}).json()
    kept = client.post("/api/assistant/page/keep", json={"turn_id": turn["turn_id"], "page": page}).json()
    assert kept["paper_id"].startswith("w_") and kept["quote"] == "look back at its own failed attempts"
    assert library.has_item("", kept["paper_id"], owner="local")
    note = annotations.get(kept["ann_id"])
    assert (note["text"], note["comment"], note["state"]) == ("look back at its own failed attempts", "What does this mean?", "kept")
    assert [(x["who"], x["text"]) for x in annotations.replies(kept["ann_id"])] == [("Pippin", "It learns from failed attempts.")]
    again = client.post("/api/assistant/page/keep", json={"turn_id": turn["turn_id"], "page": page}).json()
    assert again["ann_id"] == kept["ann_id"]                      # kept once
    assert client.get("/api/assistant/history").json()[-1]["kept"]["ann_id"] == kept["ann_id"]
    assert client.post("/api/assistant/page/keep", json={"turn_id": "nope", "page": page}).status_code == 404


def test_without_a_selection_the_note_hangs_on_the_passage_the_answer_drew_on(client, model):
    model["m"] = Scripted([[("this_page", {"question": "reflection failed attempts"})], "Reflection."])
    page = {"url": URL, "title": "LLM Powered Agents", "text": TEXT}
    turn = client.post("/api/assistant", json={"text": "reflection?", "page": page}).json()
    kept = client.post("/api/assistant/page/keep", json={"turn_id": turn["turn_id"], "page": page}).json()
    assert kept["quote"] and kept["quote"] in TEXT


def test_a_papers_page_is_read_from_its_pdf_and_opened_in_pippin(client, model, monkeypatch):
    from aurora.api.routes import reader
    from aurora.memory import relate
    stored = library.upsert_paper(Paper(title="Reflexion", authors=["Noah Shinn"], year=2023, arxiv_id="2303.11366"))

    async def paper(url):
        return {"paper_id": stored["paper_id"], "title": "Reflexion"} if "arxiv.org" in url else None

    async def texts(entry, cid):
        return ["Abstract. Agents reflect.", "Section 3. Verbal reinforcement stores reflections in an episodic memory."]
    monkeypatch.setattr(relate, "_paper", paper)
    monkeypatch.setattr(reader, "_texts", texts)
    model["m"] = Scripted([[("this_page", {"question": "where are reflections stored"})], "In an episodic memory (page 2)."])
    page = {"url": "https://arxiv.org/abs/2303.11366", "title": "[2303.11366] Reflexion", "text": "Only the abstract."}
    client.post("/api/assistant", json={"text": "where are they stored?", "page": page})
    tool = model["m"].sent[1][-1]["content"]
    assert "the paper's PDF" in tool and "[page 2]" in tool and "Only the abstract" not in tool
    opened = client.post("/api/assistant/page/open", json=page).json()
    assert opened["paper_id"] == stored["paper_id"] and not opened["is_page"]
    assert library.has_item("", stored["paper_id"], owner="local")


def test_passages_of_a_long_text_put_the_selection_first():
    import asyncio
    asyncio.run(onpage.load("u1", url=URL, title="A", text=TEXT, selection=REFLECT))
    got = onpage.passages("u1", "unrelated words")
    assert any(REFLECT[:40] in p["text"] for p in got["passages"]) and got["selected"] == REFLECT
    assert onpage.passages("u2", "x") == {"error": "No page was sent from the browser with this question."}
