"""Pre-flight: every job is checked (prerequisites, smoke run) before research; problems go back to
planning, then to the researcher; the findings reach the research agents."""

import asyncio
import json

from aurora.config import AuroraConfig
from aurora.core.engine import ResearchEngine
from aurora.models.experiment import ExperimentJob, ExperimentPlan

PLAN = ExperimentPlan(plan_id="p", proposal_id="x", jobs=[
    ExperimentJob(job_id="fetch", skill_name="download the data"),
    ExperimentJob(job_id="clean", skill_name="clean it", dependencies=["fetch"]),
])


def make(tmp_path, reports, **cfg):
    """An engine whose pre-flight agent writes *reports* in turn (None: writes nothing)."""
    engine = ResearchEngine(AuroraConfig(objective="x", mock_mode=True, campaign_id="pf",
                                         workspace={"root": str(tmp_path)}, **cfg))
    engine._setup()
    engine._should_use_agent_session = lambda: True
    calls = {"sessions": 0, "replans": []}

    async def session(phase, system, user, **kw):
        attempt = calls["sessions"]
        calls["sessions"] += 1
        report = reports[min(attempt, len(reports) - 1)]
        if report == "sleep":
            await asyncio.sleep(60)
        elif report is not None:
            engine._preflight_report_path(attempt).write_text(json.dumps(report))
        return {}

    async def replan(proposal, guidance=""):
        calls["replans"].append(guidance)
        return PLAN

    engine._run_agent_session = session
    engine._run_planning = replan
    return engine, calls


def job(job_id, status, **extra):
    return {"job_id": job_id, "status": status, "smoke_run": "1% sample, 4 s, 300 MB", **extra}


async def test_problems_go_back_to_planning_then_findings_reach_the_jobs(tmp_path):
    first = {"jobs": [job("fetch", "ok"), job("clean", "problem", problems=["OOM at full size: 40 GB"])]}
    second = {"jobs": [job("fetch", "ok", notes_for_research="mirror is faster"),
                       job("clean", "ok", estimate="20 min, 6 GB")]}
    engine, calls = make(tmp_path, [first, second])
    plan, problems = await engine._run_preflight(PLAN, None)
    assert problems == [] and calls["sessions"] == 2
    assert "OOM at full size" in calls["replans"][0]                   # the report went to planning
    notes = {j.job_id: j.parameters["preflight"] for j in plan.jobs}
    assert notes["fetch"]["notes_for_research"] == "mirror is faster"
    assert notes["clean"]["estimate"] == "20 min, 6 GB"
    saved = ExperimentPlan.model_validate_json(engine._workspace.experiment_plan_path_for_round(0).read_text())
    assert saved.jobs[1].parameters["preflight"]["status"] == "ok"   # research reads the plan from disk


async def test_problems_left_after_the_revisions_are_returned(tmp_path):
    bad = {"jobs": [job("fetch", "ok"), job("clean", "problem", problems=["schema changed in 2024"])]}
    engine, calls = make(tmp_path, [bad], agent={"preflight_max_revisions": 1})
    plan, problems = await engine._run_preflight(PLAN, None)
    assert calls["sessions"] == 2 and len(calls["replans"]) == 1
    assert problems == ["clean: schema changed in 2024"]


async def test_jobs_missing_from_the_report_are_untestable(tmp_path):
    engine, _ = make(tmp_path, [{"jobs": [job("fetch", "ok")]}])
    plan, problems = await engine._run_preflight(PLAN, None)
    assert problems == [] and plan.jobs[1].parameters["preflight"]["status"] == "untestable"


async def test_time_limit_keeps_the_report_so_far(tmp_path):
    engine, _ = make(tmp_path, ["sleep"], agent={"preflight_timeout_minutes": 0.01})
    engine._preflight_report_path(0).write_text(json.dumps({"jobs": [job("fetch", "ok")]}))
    plan, problems = await asyncio.wait_for(engine._run_preflight(PLAN, None), 30)
    assert plan.jobs[0].parameters["preflight"]["status"] == "ok"
    assert plan.jobs[1].parameters["preflight"]["status"] == "untestable"


async def test_open_problems_ask_the_researcher(tmp_path):
    """In a campaign, problems left after the revisions open a gate; continue runs the plan."""
    bad = {"jobs": [job("fetch", "problem", problems=["the URL is gone"]), job("clean", "ok")]}
    engine = ResearchEngine(AuroraConfig(objective="x", mock_mode=True, campaign_id="pf2",
                                         workspace={"root": str(tmp_path)}, human={"autonomy": "supervised"},
                                         skip_phases=["ideation", "screening", "writing"],
                                         agent={"preflight_max_revisions": 0}))
    phases = []

    async def preflight(plan, proposal, guidance=""):
        engine._workspace.phase_round_dir("preflight", 0)
        phases.append("preflight")
        return plan, ["fetch: the URL is gone"]

    async def record(event):
        if event.name == "phase_started":
            phases.append(event.data["phase"])

    engine._run_preflight = preflight
    engine.event_bus.subscribe_all(record)
    task = asyncio.create_task(engine.run_campaign())
    for _ in range(300):
        await asyncio.sleep(0.05)
        if engine.human_gate.pending:
            break
    [gate] = engine.human_gate.pending
    assert gate.source == "preflight" and "the URL is gone" in gate.detail
    engine.human_gate.resolve(gate.gate_id, "continue", "use the mirror")
    result = await asyncio.wait_for(task, 60)
    assert result.status == "completed", result.summary
    assert phases.index("preflight") < phases.index("research")
    assert bad
