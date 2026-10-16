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
