"""Interrupted work continues where it stopped instead of starting over."""

import asyncio

import pytest

from aurora.agents.base import AgentContext
from aurora.agents.roundtable.roundtable import Roundtable
from aurora.agents.roundtable.seat import RoundtableSeat
from aurora.config import AuroraConfig
from aurora.core import engine as engine_module
from aurora.core.engine import ResearchEngine
from aurora.llm.router import create_router
from aurora.state.sessions import CONTINUE_NOTE, RESTART_NOTE


async def test_meeting_resumes_after_the_items_already_discussed(tmp_path, monkeypatch):
    cfg = AuroraConfig(objective="Zn segregation in Cu(111)", mock_mode=True)
    state = tmp_path / "meeting_state.json"
    ctx = AgentContext(campaign_id="c", current_goal="Zn segregation in Cu(111)", research_summary="")
    prepares = []
    original_prepare = RoundtableSeat.prepare

    async def counting_prepare(self, *a, **kw):
        prepares.append(self.agent_id)
        return await original_prepare(self, *a, **kw)

    monkeypatch.setattr(RoundtableSeat, "prepare", counting_prepare)

    async def stop_after_first_item(name, step, **data):
        if step == "item_notes":
            raise asyncio.CancelledError

    first = Roundtable(cfg, create_router(cfg), state_path=state)
    first._emit = lambda step, **d: stop_after_first_item("ideation_progress", step, **d)
    with pytest.raises(asyncio.CancelledError):
        await first.deliberate(ctx)
    assert state.exists() and len(prepares) == 3

    steps = []

    async def record(name, step, **data):
        steps.append(step)

    second = Roundtable(cfg, create_router(cfg), progress=record, state_path=state)
    proposal = await second.deliberate(ctx)
    assert len(prepares) == 3, "ideas were prepared again"
    assert steps[0] == "resumed" and steps.count("agenda_item") == 2
    assert proposal.decision and not state.exists()
    assert (tmp_path / "meeting_state.finished.json").exists()


class FakeSession:
    calls: list[dict] = []
    results: list[dict] = []

    def __init__(self, config, bus, **kw):
        self.kw = kw

    async def run(self, system_prompt, user_message):
        FakeSession.calls.append({"resume": self.kw.get("resume_session_id"), "message": user_message,
                                  "system": system_prompt})
        if self.kw.get("session_callback"):
            self.kw["session_callback"]("new-session")
        return FakeSession.results.pop(0)


@pytest.fixture()
def real_mode_engine(tmp_path, monkeypatch):
    monkeypatch.setattr(engine_module, "AgentSession", FakeSession)
    FakeSession.calls, FakeSession.results = [], []
    eng = ResearchEngine(AuroraConfig(objective="x", campaign_id="c", workspace={"root": str(tmp_path)},
                                      sandbox={"docker": False}))
    eng._setup()
    return eng


async def test_interrupted_phase_session_is_continued(real_mode_engine):
    eng = real_mode_engine
    eng._sessions.record("planning:round0", "old-session", "PLANNING PROMPT")
    eng._resuming = True
    FakeSession.results = [{"turns": 5, "cost_usd": 0.1, "error": None}]
    await eng._run_agent_session("planning", "PLANNING PROMPT", "write the plan")
    call = FakeSession.calls[0]
    assert call["resume"] == "old-session" and call["message"].startswith(CONTINUE_NOTE)
    assert eng._sessions.open_session("planning:round0") is None


async def test_unrecoverable_session_starts_over_with_a_hint(real_mode_engine):
    eng = real_mode_engine
    eng._sessions.record("analysis:round0", "lost-session", "ANALYSIS PROMPT")
    eng._resuming = True
    FakeSession.results = [{"turns": 0, "error": "No conversation found"}, {"turns": 7, "error": None}]
    await eng._run_agent_session("analysis", "ANALYSIS PROMPT", "analyse")
    assert [c["resume"] for c in FakeSession.calls] == ["lost-session", None]
    assert FakeSession.calls[1]["message"].startswith(RESTART_NOTE)
