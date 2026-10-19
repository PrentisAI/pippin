"""Ideation turns are streamed to the console while written, and reported in full when done."""

import asyncio

from aurora.agents.base import AgentContext
from aurora.agents.roundtable.seat import RoundtableSeat, headline
from aurora.api.routes.campaigns import ExperimentRunState
from aurora.llm.base import LLMProvider, LLMResponse, TokenUsage

TEXT = "## 3. Main research idea: E-bikes as a friction isolator\n**Question:** Do e-bikes flatten seasonality?"


class StreamingProvider(LLMProvider):
    provider_name = "fake"
    supports_streaming = True

    async def generate(self, messages, *, model=None, on_delta=None, **kwargs):
        for piece in ("thinking...", *TEXT.split(" ")):
            kind = "reasoning" if piece == "thinking..." else "content"
            res = on_delta(kind, piece + ("" if kind == "reasoning" else " ")) if on_delta else None
            if res is not None:
                await res
            await asyncio.sleep(0)
        return LLMResponse(content=TEXT, model_id=model or "fake/model", usage=TokenUsage(), raw_response=None)

    async def generate_structured(self, *a, **k):
        raise NotImplementedError

    async def close(self):
        pass


class FakeRouter:
    def __init__(self, provider):
        self.provider = provider

    def get_provider_and_model(self, stage, seat_index=0):
        return self.provider, "fake/model"

    async def generate(self, stage, messages, *, seat_index=0, **kwargs):
        from aurora.llm.router import LLMRouter
        return await LLMRouter.generate(self, stage, messages, seat_index=seat_index, **kwargs)


def test_headline_names_the_main_idea():
    assert headline(TEXT) == "E-bikes as a friction isolator"
    assert headline("Title: **Footprint-robust atlas**\nmore") == "Footprint-robust atlas"
    assert headline("## 3. Main idea\n- **Question:** does X cause Y?") == "Does X cause Y?"
    assert headline("We should look at weather. Then more.") == "We should look at weather."


async def test_seat_streams_its_turn_and_reports_it_when_done():
    events = []

    async def progress(name, **data):
        events.append((name, data))

    seat = RoundtableSeat(FakeRouter(StreamingProvider()), seat_index=0)
    seat.on_progress = progress
    contribution = await seat.prepare(AgentContext(campaign_id="c", current_goal="Citi Bike usage"), "Citi Bike usage")

    stream = [d for n, d in events if n == "ideation_stream"]
    turns = [d for n, d in events if n == "ideation_turn"]
    assert stream and stream[0]["text"] == ""                       # announced before the first words
    assert stream[0]["seat"] == "p01" and stream[0]["kind"] == "presentation"
    assert len(turns) == 1 and events[-1][0] == "ideation_turn"
    assert turns[0]["content"] == contribution.content == TEXT
    assert turns[0]["headline"] == "E-bikes as a friction isolator"


async def test_seat_without_streaming_still_reports_the_turn():
    class Plain(StreamingProvider):
        supports_streaming = False

        async def generate(self, messages, *, model=None, **kwargs):
            assert "on_delta" not in kwargs                            # the router drops it
            return LLMResponse(content="PASS", model_id="fake/model", usage=TokenUsage(), raw_response=None)

    events = []

    async def progress(name, **data):
        events.append(name)

    seat = RoundtableSeat(FakeRouter(Plain()), seat_index=1)
    seat.on_progress = progress
    await seat.remark(AgentContext(campaign_id="c", current_goal="t"), "t", "", "p01", 1)
    assert events == ["ideation_stream", "ideation_turn"]


async def test_live_previews_are_not_stored(monkeypatch):
    stored = []
    from aurora.api.routes import campaigns
    monkeypatch.setattr(campaigns.store, "append_event", lambda c, r, e: stored.append(e["event_type"]))
    run = ExperimentRunState("e1", "c1", "task", "fake/model")
    await run.broadcast({"event_type": "ideation_stream", "data": {}})
    await run.broadcast({"event_type": "ideation_turn", "data": {}})
    assert stored == ["ideation_turn"]
    assert [e["event_type"] for e in run.events] == ["ideation_turn"]
