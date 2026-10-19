"""Research jobs run in parallel when the plan's dependencies allow it (agent.max_parallel_jobs)."""

import asyncio
import time

from aurora.config import AuroraConfig
from aurora.core import engine as engine_mod
from aurora.core.engine import ResearchEngine
from aurora.core.lifecycle import IterationResult
from aurora.models.experiment import ExperimentJob, ExperimentPlan, JobResult

#   a ─┬─> b ─┬─> d
#      └─> c ─┘
DIAMOND = ExperimentPlan(plan_id="p", proposal_id="x", jobs=[
    ExperimentJob(job_id="a", skill_name="get data"),
    ExperimentJob(job_id="b", skill_name="analysis 1", dependencies=["a"]),
    ExperimentJob(job_id="c", skill_name="analysis 2", dependencies=["a"]),
    ExperimentJob(job_id="d", skill_name="report", dependencies=["b", "c"]),
])


def make(tmp_path, monkeypatch, parallel, seconds=0.3, durations=None):
    spans: dict[str, list[float]] = {}

    class FakeIteration:
        def __init__(self, **kwargs):
            pass

        async def run(self, job, plan, n=0, **kwargs):
            spans[job.job_id] = [time.monotonic()]
            await asyncio.sleep((durations or {}).get(job.job_id, seconds))
            spans[job.job_id].append(time.monotonic())
            return IterationResult(iteration_id=f"i_{job.job_id}", job_id=job.job_id, decision="done",
                                   job_result=JobResult(job_id=job.job_id, success=True))

    monkeypatch.setattr(engine_mod, "ResearchIteration", FakeIteration)
    cfg = AuroraConfig(objective="x", mock_mode=True, campaign_id=f"par{parallel}", workspace={"root": str(tmp_path)},
                       skip_phases=["ideation", "screening", "writing"], agent={"max_parallel_jobs": parallel})
    engine = ResearchEngine(cfg)

    async def plan(proposal, guidance=""):
        return DIAMOND

    engine._run_planning = plan
    return engine, spans


def overlap(s1, s2):
    return min(s1[1], s2[1]) - max(s1[0], s2[0]) > 0


async def test_independent_jobs_run_at_the_same_time(tmp_path, monkeypatch):
    engine, spans = make(tmp_path, monkeypatch, parallel=2)
    started = []

    async def record(event):
        if event.name == "job_started":
            started.append((event.data["job_id"], event.data["parallel_with"]))

    engine.event_bus.subscribe_all(record)
    result = await asyncio.wait_for(engine.run_campaign(), 60)
    assert result.status == "completed", result.summary
    assert set(spans) == {"a", "b", "c", "d"}
    assert overlap(spans["b"], spans["c"])                                  # b and c together
    assert spans["d"][0] >= max(spans["b"][1], spans["c"][1])               # d after both
    assert spans["b"][0] >= spans["a"][1] and spans["c"][0] >= spans["a"][1]
    assert ("c", ["b"]) in started or ("b", ["c"]) in started


async def test_one_at_a_time_by_default(tmp_path, monkeypatch):
    engine, spans = make(tmp_path, monkeypatch, parallel=1)
    result = await asyncio.wait_for(engine.run_campaign(), 60)
    assert result.status == "completed", result.summary
    assert not overlap(spans["b"], spans["c"])


async def test_stop_ends_every_running_job(tmp_path, monkeypatch):
    engine, spans = make(tmp_path, monkeypatch, parallel=2, seconds=30, durations={"a": 0.1})
    task = asyncio.create_task(engine.run_campaign())
    for _ in range(300):
        await asyncio.sleep(0.05)
        if "b" in spans and "c" in spans:
            break
    engine.request_stop("stopped by the user")
    result = await asyncio.wait_for(task, 20)
    assert result.status == "stopped"
    assert len(spans["b"]) == 1 and len(spans["c"]) == 1                    # both cancelled, not finished


async def test_messages_reach_one_job_or_all():
    from aurora.core.terminal_iteration import RunningJobs
    got = {"b": [], "c": []}

    class R:
        def __init__(self, name):
            self.name = name
            self._pending_messages = []

        async def inject_message(self, m):
            got[self.name].append(m)

    group = RunningJobs()
    await group.inject_message("before any job")                            # waits for the next job
    group.add("b", R("b"))
    assert group.active["b"]._pending_messages == ["before any job"]
    group.add("c", R("c"))
    await group.inject_message("to c", "c")
    await group.inject_message("to all")
    assert got == {"b": ["to all"], "c": ["to c", "to all"]}


def test_dependencies_written_on_the_jobs_are_respected():
    """Planning agents put dependencies on the jobs, not in dependency_edges."""
    from aurora.planning.dependency_graph import DependencyGraph
    dag = DependencyGraph(DIAMOND)
    assert [j.job_id for j in dag.ready_jobs()] == ["a"]
    bad = ExperimentPlan(plan_id="q", proposal_id="x", jobs=[
        ExperimentJob(job_id="a", skill_name="s", dependencies=["nonexistent"])])
    assert [j.job_id for j in DependencyGraph(bad).ready_jobs()] == ["a"]     # unknown: ignored
