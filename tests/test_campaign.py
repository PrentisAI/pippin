import json

import pytest

from aurora.cli import main
from aurora.config import AuroraConfig
from aurora.core.engine import ResearchEngine


async def test_mock_campaign_runs_every_phase(tmp_path):
    cfg = AuroraConfig(objective="Investigate Zn segregation in Cu(111)", mock_mode=True,
                       campaign_id="t1", workspace={"root": str(tmp_path / "ws")})
    events = []
    engine = ResearchEngine(cfg)

    async def record(event):
        events.append(event.name)

    engine.event_bus.subscribe_all(record)
    result = await engine.run_campaign()
    assert result.status == "completed", result.summary
    assert result.iterations and all(r.job_result.success for r in result.iterations)
    for name in ("ideation_complete", "screening_complete", "planning_complete", "milestone_audit",
                 "research_loop_complete", "analysis_complete", "writing_complete", "campaign_completed"):
        assert name in events, name
    campaign = tmp_path / "ws" / "t1"
    assert (campaign / "config.yaml").exists() and "api_key" not in (campaign / "config.yaml").read_text()
    assert json.loads((campaign / "checkpoint.json").read_text())["phase"] == "completed"
    assert result.manuscript_path and (campaign / "phases" / "writing").exists()


async def test_skipping_phases(tmp_path):
    cfg = AuroraConfig(objective="x", mock_mode=True, campaign_id="t2", workspace={"root": str(tmp_path)},
                       skip_phases=["ideation", "screening", "writing"])
    result = await ResearchEngine(cfg).run_campaign()
    assert result.status == "completed" and result.manuscript_path is None


async def test_budget_stops_the_campaign(tmp_path):
    cfg = AuroraConfig(objective="x", mock_mode=True, campaign_id="t3", workspace={"root": str(tmp_path)},
                       budget={"max_hours": 1e-9})
    result = await ResearchEngine(cfg).run_campaign()
    assert result.status == "stopped" and "time budget" in result.summary


def test_cli_demo_and_status(tmp_path, capsys):
    try:
        main(["demo", "--workspace", str(tmp_path / "ws"), "--name", "demo"])
    except SystemExit as exc:
        assert exc.code == 0
    assert "Campaign COMPLETED" in capsys.readouterr().out
    try:
        main(["status", str(tmp_path / "ws" / "demo")])
    except SystemExit as exc:
        assert exc.code == 0
    assert "Next phase : completed" in capsys.readouterr().out


def test_cli_init_writes_a_valid_config(tmp_path):
    try:
        main(["init", str(tmp_path / "a.yaml")])
    except SystemExit as exc:
        assert exc.code == 0
    from aurora.config import load_config
    assert load_config(tmp_path / "a.yaml").ideation.mode == "roundtable"


async def test_independent_ideation(tmp_path):
    cfg = AuroraConfig(objective="Investigate Zn segregation in Cu(111)", mock_mode=True, campaign_id="t4",
                       workspace={"root": str(tmp_path)}, ideation={"mode": "independent"},
                       skip_phases=["planning", "research", "analysis", "writing"])
    result = await ResearchEngine(cfg).run_campaign()
    assert result.status == "completed", result.summary
    assert result.proposal.decision["mode"] == "independent"


async def test_stop_cancels_a_long_phase_promptly(tmp_path):
    import asyncio
    import time

    cfg = AuroraConfig(objective="x", mock_mode=True, campaign_id="t5", workspace={"root": str(tmp_path)})
    engine = ResearchEngine(cfg)

    async def slow_ideation(guidance=""):
        await asyncio.sleep(60)

    engine._run_roundtable = slow_ideation
    task = asyncio.create_task(engine.run_campaign())
    await asyncio.sleep(0.5)
    started = time.time()
    engine.request_stop("stopped by the user")
    result = await asyncio.wait_for(task, timeout=10)
    assert result.status == "stopped" and "stopped by the user" in result.summary
    assert time.time() - started < 5


async def test_stop_then_resume_continues_from_the_checkpoint(tmp_path):
    import asyncio

    cfg = AuroraConfig(objective="Investigate Zn segregation", mock_mode=True, campaign_id="t6",
                       workspace={"root": str(tmp_path)}, skip_phases=["ideation"])
    first = ResearchEngine(cfg)

    async def slow_analysis():
        await asyncio.sleep(60)

    first._run_analysis = slow_analysis
    task = asyncio.create_task(first.run_campaign())
    for _ in range(100):
        await asyncio.sleep(0.1)
        if first.phase.value == "analysis":
            break
    first.request_stop("stopped by the user")
    assert (await asyncio.wait_for(task, timeout=10)).status == "stopped"
    checkpoint = json.loads((tmp_path / "t6" / "checkpoint.json").read_text())
    assert checkpoint["phase"] == "analysis"

    second = ResearchEngine(cfg)
    phases = []

    async def record(event):
        if event.name == "phase_started":
            phases.append(event.data["phase"])

    second.event_bus.subscribe_all(record)
    result = await second.run_campaign(resume=True)
    assert result.status == "completed", result.summary
    assert phases == ["analysis", "writing"]
    assert result.manuscript_path


def _gate_engine(tmp_path, cid, monkeypatch, reading=None):
    """A campaign whose screening rejects everything; *reading* is how the model reads the
    researcher's direction (None: the model call fails)."""
    from aurora.core import engine as engine_mod
    from aurora.llm.router import LLMRouter

    real = LLMRouter.generate_structured

    async def fake(self, stage, messages, schema, **kw):
        if schema is engine_mod.ResearcherDirection:
            if reading is None:
                raise RuntimeError("model unavailable")
            return reading(messages[-1].content)
        return await real(self, stage, messages, schema, **kw)

    monkeypatch.setattr(LLMRouter, "generate_structured", fake)
    cfg = AuroraConfig(objective="Citi Bike usage", mock_mode=True, campaign_id=cid,
                       workspace={"root": str(tmp_path)}, human={"autonomy": "supervised"})
    engine = ResearchEngine(cfg)
    screened, planned = [], []

    async def reject(proposal):
        screened.append(proposal)
        return proposal.model_copy(update={"selected": False})

    real_planning = engine._run_planning

    async def plan(proposal, guidance=""):
        planned.append(proposal)
        return await real_planning(proposal, guidance)

    engine._run_screening = reject
    engine._run_planning = plan
    return engine, screened, planned


async def _answer_gate(engine, action, message, rerun=None):
    import asyncio

    task = asyncio.create_task(engine.run_campaign())
    for _ in range(200):
        await asyncio.sleep(0.05)
        if engine.human_gate.pending:
            break
    [gate] = engine.human_gate.pending
    assert "propose" in gate.options
    engine.human_gate.resolve(gate.gate_id, action, message, rerun=rerun)
    return await asyncio.wait_for(task, timeout=60)


async def test_researcher_picks_a_proposal_at_the_screening_gate(tmp_path, monkeypatch):
    """'The latest one is fine' means the latest proposal, not a proposal titled with that sentence."""
    import re

    from aurora.core.engine import ResearcherDirection

    def read(request):
        latest = re.findall(r"### (prop_\w+) \(the latest\)", request)[0]
        return ResearcherDirection(basis=latest, interpretation="continue with the latest proposal",
                                   changes="also look at weather", title="", description="")

    engine, screened, planned = _gate_engine(tmp_path, "t7", monkeypatch, read)
    result = await _answer_gate(engine, "propose", "I think the latest proposal is fine, add weather")
    assert result.status == "completed", result.summary
    assert len(screened) == 3                                     # no screening after the decision
    chosen = planned[0]
    assert chosen.title == screened[-1].title                     # the latest proposal, kept
    assert chosen.decision["basis"] == screened[-1].proposal_id
    assert "also look at weather" in chosen.description
    assert engine._load_latest_proposal().proposal_id == chosen.proposal_id
    assert "The researcher chose this direction" in engine._campaign_brief(chosen)


async def test_researcher_direction_falls_back_to_the_message(tmp_path, monkeypatch):
    engine, _, planned = _gate_engine(tmp_path, "t7b", monkeypatch, None)
    my_idea = "E-bikes as a friction isolator\nDo e-bikes flatten seasonality and weather response?"
    result = await _answer_gate(engine, "propose", my_idea)
    assert result.status == "completed", result.summary
    assert planned[0].title == "E-bikes as a friction isolator" and planned[0].description == my_idea


async def test_continue_with_a_note_keeps_the_proposal(tmp_path, monkeypatch):
    engine, screened, planned = _gate_engine(tmp_path, "t7c", monkeypatch, None)
    notes = []
    real_planning = engine._run_planning

    async def plan(proposal, guidance=""):
        notes.append(guidance)
        return await real_planning(proposal, guidance)

    engine._run_planning = plan
    result = await _answer_gate(engine, "continue", "keep it short")
    assert result.status == "completed", result.summary
    assert len(screened) == 3 and planned[0].proposal_id == screened[-1].proposal_id
    assert "keep it short" in notes[0]


@pytest.mark.parametrize("rerun", [["ideation"], ["screening"], ["ideation", "screening"]])
async def test_chosen_direction_can_go_through_ideation_or_screening_again(tmp_path, monkeypatch, rerun):
    engine, screened, planned = _gate_engine(tmp_path, f"t7{len(rerun)}{rerun[0][0]}", monkeypatch, None)
    rejected = list(screened)
    ideation_guidance = []
    real_ideation = engine._run_roundtable

    async def ideation(guidance=""):
        ideation_guidance.append(guidance)
        return await real_ideation(guidance)

    async def screen(proposal):           # rejects ideation's proposals, passes everything after the gate
        rejected.append(proposal)
        passed = len(rejected) > 3
        return proposal.model_copy(update={"selected": passed})

    engine._run_roundtable = ideation
    engine._run_screening = screen
    result = await _answer_gate(engine, "propose", "E-bikes as a friction isolator", rerun)
    assert result.status == "completed", result.summary
    assert len(ideation_guidance) == 3 + ("ideation" in rerun)
    if "ideation" in rerun:
        assert "E-bikes as a friction isolator" in ideation_guidance[-1]
        assert "do not propose a different direction" in ideation_guidance[-1]
    assert len(rejected) == 3 + ("screening" in rerun)
    if rerun == ["screening"]:
        assert planned[0].title == "E-bikes as a friction isolator"


async def test_resume_at_the_screening_gate_does_not_screen_again(tmp_path):
    """Stopped while the screening gate waits: the resumed campaign asks again, with no new
    ideation or screening, and keeps the verdicts."""
    import asyncio

    cfg = AuroraConfig(objective="Citi Bike usage", mock_mode=True, campaign_id="t8",
                       workspace={"root": str(tmp_path)}, human={"autonomy": "supervised"})

    def make(screen_log, ideation_log):
        engine = ResearchEngine(cfg)
        real_ideation = engine._run_roundtable

        async def ideation(guidance=""):
            ideation_log.append(guidance)
            return await real_ideation(guidance)

        async def reject(proposal):
            screen_log.append(proposal.proposal_id)
            from aurora.models.roundtable import IdeaScore
            score = IdeaScore(novelty=4, feasibility=8, significance=4, composite=5.6, explanation="vanilla")
            return proposal.model_copy(update={"selected": False, "score": score})

        engine._run_roundtable = ideation
        engine._run_screening = reject
        return engine

    async def wait_gate(engine):
        for _ in range(300):
            await asyncio.sleep(0.05)
            if engine.human_gate.pending:
                return engine.human_gate.pending[0]
        raise AssertionError("no gate opened")

    screened, ideas = [], []
    first = make(screened, ideas)
    task = asyncio.create_task(first.run_campaign())
    await wait_gate(first)
    first.request_stop("stopped by the user")
    assert (await asyncio.wait_for(task, timeout=10)).status == "stopped"
    assert len(screened) == 3 and len(ideas) == 3

    screened2, ideas2 = [], []
    second = make(screened2, ideas2)
    task = asyncio.create_task(second.run_campaign(resume=True))
    gate = await wait_gate(second)
    assert screened2 == [] and ideas2 == []                       # straight back to the decision
    second.human_gate.resolve(gate.gate_id, "propose", "My own idea\nwith details")
    result = await asyncio.wait_for(task, timeout=60)
    assert result.status == "completed", result.summary
    assert second._load_latest_proposal().title == "My own idea"


async def test_cost_carries_over_to_a_resumed_campaign(tmp_path):
    cfg = AuroraConfig(objective="x", mock_mode=True, campaign_id="t9", workspace={"root": str(tmp_path)},
                       skip_phases=["ideation", "screening", "writing"], budget={"max_cost_usd": 5})
    (tmp_path / "t9").mkdir(parents=True)
    (tmp_path / "t9" / "cost.json").write_text('{"cost_usd": 4.5}')
    (tmp_path / "t9" / "checkpoint.json").write_text('{"campaign_id": "t9", "phase": "planning", "timestamp": "2026-10-19T00:00:00Z"}')
    engine = ResearchEngine(cfg)
    engine.cost_usd = 0.6            # this run's own spending pushes the total over the budget
    result = await engine.run_campaign(resume=True)
    assert result.status == "stopped" and "budget" in result.summary
    assert result.cost_usd >= 5.1
    assert json.loads((tmp_path / "t9" / "cost.json").read_text())["cost_usd"] >= 5.1


async def test_unanswered_live_auditor_question_does_not_break_the_finish(tmp_path):
    """A live-auditor gate left open when research ends is closed; the campaign still finishes
    cleanly (it used to be cancelled while cleaning up and stay 'running')."""
    import asyncio

    cfg = AuroraConfig(objective="x", mock_mode=True, campaign_id="t10", workspace={"root": str(tmp_path)},
                       skip_phases=["ideation", "screening"], human={"autonomy": "supervised"})
    engine = ResearchEngine(cfg)
    resolved = []
    real_loop = engine._run_research_loop

    async def loop_with_question(*args, **kwargs):
        # the live auditor asks the researcher during research, and nobody answers
        asyncio.create_task(engine.human_gate.request("supervisor", "The live auditor escalated", "d"))
        while not engine.human_gate.pending:
            await asyncio.sleep(0.01)
        return await real_loop(*args, **kwargs)

    async def on_event(event):
        if event.name == "human_gate_resolved":
            resolved.append((event.data["source"], event.data["decided_by"], event.data["action"]))

    engine._run_research_loop = loop_with_question
    engine.event_bus.subscribe_all(on_event)
    result = await asyncio.wait_for(engine.run_campaign(), timeout=60)
    assert result.status == "completed", result.summary
    assert resolved == [("supervisor", "system", "continue")]
    assert engine.human_gate.pending == []


async def test_a_stop_while_cleaning_up_does_not_cancel_the_clean_up(tmp_path):
    import asyncio

    cfg = AuroraConfig(objective="x", mock_mode=True, campaign_id="t11", workspace={"root": str(tmp_path)},
                       skip_phases=["ideation", "screening"])
    engine = ResearchEngine(cfg)
    real_stop = engine._stop_frontdesk

    async def stop_frontdesk():
        async def late_stop():                   # e.g. a gate answered 'stop' as the campaign ends
            engine.request_stop("a late stop")

        await asyncio.create_task(late_stop())
        await asyncio.sleep(0.05)
        await real_stop()

    engine._stop_frontdesk = stop_frontdesk
    result = await asyncio.wait_for(engine.run_campaign(), timeout=60)
    assert result.status == "completed", result.summary
