"""The start page's conversation: what the person means by what they typed."""

from aurora import home
from tests.test_api import client  # noqa: F401


def test_the_answer_is_read_from_json_even_with_text_around_it():
    assert home.parse('Sure.\n{"intent": "unsure", "reply": "That is a fine place to start."}') == \
        {"intent": "unsure", "reply": "That is a fine place to start."}
    assert home.parse('{"intent": "research", "reply": "x"}') is None
    assert home.parse("no json here") is None


def test_the_start_page_asks_the_model(client, monkeypatch):  # noqa: F811
    seen = {}

    async def fake(messages, **kw):
        seen["prompt"] = messages[-1]["content"]
        return '{"intent": "unsure", "reply": "That is a fine place to start."}'

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-" + "a" * 40)
    monkeypatch.setattr(home, "_ask_model", fake)
    r = client.post("/api/home/reply", json={"text": "I have no idea", "history": [
        {"from": "bot", "text": "What would you like to find out?"}]})
    assert r.status_code == 200, r.text
    assert r.json() == {"intent": "unsure", "reply": "That is a fine place to start."}
    assert "I have no idea" in seen["prompt"] and "What would you like to find out?" in seen["prompt"]


def test_without_a_key_or_a_model_the_page_goes_on(client, monkeypatch):  # noqa: F811
    async def broken(messages, **kw):
        raise RuntimeError("offline")

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert client.post("/api/home/reply", json={"text": "hello"}).json()["intent"] is None
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-" + "a" * 40)
    monkeypatch.setattr(home, "_ask_model", broken)
    assert client.post("/api/home/reply", json={"text": "hello"}).json()["intent"] is None
