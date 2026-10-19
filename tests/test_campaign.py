import json

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
