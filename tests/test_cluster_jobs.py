"""Cluster jobs: limits, submission, following a job to its end, cancelling — against fake SLURM tools."""

import json
import os
import stat
import sys

import pytest

from aurora.compute import job_client
from aurora.compute.slurm import SlurmCompute, parse_time, saved_jobs
from aurora.config import SlurmConfig

# Fake SLURM: sbatch records the script and prints an ID; squeue prints what the state file says;
# scontrol reports the job finished; scancel records its arguments.
FAKE = {
    "sbatch": '#!/bin/sh\ncp "$2" "$FAKE/submitted.sbatch"\necho 4242\n',
    "squeue": ('#!/bin/sh\nif [ -f "$FAKE/squeue_fail" ]; then cat "$FAKE/squeue_fail" >&2; exit 1; fi\n'
               '[ -f "$FAKE/queue" ] && cat "$FAKE/queue"\nexit 0\n'),
    "scontrol": ('#!/bin/sh\nif [ -f "$FAKE/purged" ]; then echo "Invalid job id specified" >&2; exit 1; fi\n'
                 'echo "JobId=4242 JobName=x JobState=COMPLETED Reason=None ExitCode=0:0 RunTime=00:02:05"\n'),
    "scancel": '#!/bin/sh\necho "$@" > "$FAKE/cancelled"\nrm -f "$FAKE/queue"\n',
}


@pytest.fixture()
def slurm(tmp_path, monkeypatch):
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    for name, body in FAKE.items():
        f = bin_dir / name
        f.write_text(body)
        f.chmod(f.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE", str(bin_dir))
    image = tmp_path / "image.sqsh"
    image.write_bytes(b"sqsh")
    return bin_dir, image


def manager(tmp_path, image, **cfg):
    config = SlurmConfig(enabled=True, partition="gpu", partitions=["gpu-low"], job_name_prefix="alice-aurora",
                         max_gpus_per_job=2, max_jobs=1, max_time="12:00:00", **cfg)
    campaign = tmp_path / "campaign"
    (campaign / "workspace").mkdir(parents=True, exist_ok=True)
    events = []

    async def publish(topic, **data):          # as ResearchEngine._publish
        events.append((topic, data))
    m = SlurmCompute(config, campaign_dir=campaign, workspace_dir=campaign / "workspace", image=str(image),
                     publish=publish)
    return m, events


async def test_a_job_without_a_time_gets_at_most_the_longest(tmp_path, slurm):
    _, image = slurm
    m, _ = manager(tmp_path, image, default_time="04:00:00")
    m.config.max_time = "00:30:00"
    await m.start()
    try:
        assert (await m.submit("python a.py"))["job"]["time_limit"] == "00:30:00"
    finally:
        await m.stop()


def test_time_limits():
    assert parse_time("90") == 5400 and parse_time("2:00:00") == 7200 and parse_time("1-00:00:00") == 86400
    with pytest.raises(ValueError):
        parse_time("two hours")


async def test_submit_follow_and_finish(tmp_path, slurm):
    fake, image = slurm
    m, events = manager(tmp_path, image)
    await m.start()
    try:
        for bad, why in [({"gpus": 3}, "--gpus"), ({"partition": "other"}, "not allowed"),
                         ({"time_limit": "13:00:00"}, "at most 12:00:00"), ({"name": "a b"}, "--name")]:
            with pytest.raises(ValueError, match=why):
                await m.submit("python train.py", **bad)
        r = await m.submit("python train.py --lr 3e-4", name="train", gpus=1, time_limit="2:00:00")
        job = r["job"]
        assert job["slurm_id"] == "4242" and job["state"] == "PENDING" and job["cpus"] == 8 and job["mem_gb"] == 64
        script = (fake / "submitted.sbatch").read_text()
        ws = tmp_path / "campaign" / "workspace"
        assert "#SBATCH --job-name=alice-aurora-" in script and "#SBATCH --gres=gpu:1" in script
        assert "#SBATCH --partition=gpu" in script and f"--container-image={image}" in script
        assert f"--container-mounts={ws}:/app" in script
        assert (ws / ".aurora/jobs/1-train/run.sh").read_text().endswith("python train.py --lr 3e-4\n")
        with pytest.raises(ValueError, match="at most 1"):            # max_jobs
            await m.submit("python other.py")

        (fake / "queue").write_text("4242|RUNNING|1:05|None|node7\n")
        await m._poll()
        assert m.jobs[0].state == "RUNNING" and m.jobs[0].node == "node7" and m.jobs[0].elapsed_seconds == 65
        (fake / "queue").unlink()                                      # left the queue
        (ws / ".aurora/jobs/1-train/exit_code").write_text("0\n")
        await m._poll()
        j = m.jobs[0]
        assert j.state == "COMPLETED" and j.exit_code == 0 and j.elapsed_seconds == 125 and not j.active
        assert [e for e, _ in events] == ["compute_job_submitted", "compute_job_update", "compute_job_ended"]
        (ws / ".aurora/jobs/1-train/slurm.out").write_text("epoch 1\nepoch 2\nloss 0.1\n")
        assert (await m.logs(1, tail=2))["text"] == "epoch 2\nloss 0.1"
    finally:
        await m.stop()
    # the record outlives the run (the console shows finished runs' jobs)
    assert [j.state for j in saved_jobs(tmp_path / "campaign")] == ["COMPLETED"]


async def test_cancel_and_cancel_on_finish(tmp_path, slurm):
    fake, image = slurm
    m, _ = manager(tmp_path, image)
    await m.start()
    await m.submit("sleep 1000", name="long")
    (fake / "queue").write_text("4242|PENDING|0:00|Resources|\n")
    await m._poll()
    assert m.jobs[0].reason == "Resources"
    await m.stop()                                   # the experiment ended: its jobs are cancelled
    assert (fake / "cancelled").read_text().split() == [f"--name={m.jobs[0].job_name}", "4242"]


async def test_the_agents_command(tmp_path, slurm, monkeypatch, capsys):
    """`aurora job ...` as the agent runs it: through the socket, printing what it got."""
    fake, image = slurm
    m, _ = manager(tmp_path, image, max_gpu_hours=10)
    await m.start()
    try:
        env = m.agent_env()
        assert env["PATH"].startswith(os.path.dirname(sys.executable))
        monkeypatch.setenv("AURORA_COMPUTE_SOCKET", env["AURORA_COMPUTE_SOCKET"])
        prompt = m.agent_prompt()
        assert "aurora job submit" in prompt and "10 GPU hours" in prompt

        def run(*argv):
            import asyncio
            return asyncio.to_thread(job_client.main, ["job", *argv])
        assert await run("submit", "--name", "train", "--gpus", "1", "--", "python", "train.py", "--tag", "a b") == 0
        assert "SLURM job 4242" in capsys.readouterr().out
        assert m.jobs[0].command == "python train.py --tag 'a b'"
        assert await run("status") == 0
        assert "train" in capsys.readouterr().out
        assert await run("submit", "--gpus", "5", "--", "x") == 1
        assert "--gpus must be 0 to 2" in capsys.readouterr().err
    finally:
        await m.stop()
    monkeypatch.delenv("AURORA_COMPUTE_SOCKET")
    assert job_client.request({"op": "status"}, 5)["ok"] is False


def test_cluster_jobs_api(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from aurora.api.app import create_app
    from aurora.api.routes import campaigns as routes
    with TestClient(create_app()) as client:
        cid = client.post("/api/campaigns/quick", json={"prompt": "p"}).json()["campaign_id"]
        run = routes.ExperimentRunState("e1", cid, "r", "m")
        routes._ensure_campaign(cid).experiments["e1"] = run
        r = client.get(f"/api/campaigns/{cid}/experiments/e1/compute").json()
        assert r == {"enabled": False, "live": False, "jobs": [], "gpu_hours": 0}
        assert client.post(f"/api/campaigns/{cid}/experiments/e1/compute/1/cancel").status_code == 409
    assert json.dumps(r)


def test_agent_sessions_get_the_job_command(tmp_path):
    from aurora.llm.agent_loops import ClaudeCodeLoop
    loop = ClaudeCodeLoop(cwd=str(tmp_path), docker_container="abc", extra_tools=["Bash(aurora job *)"],
                          extra_env={"AURORA_COMPUTE_SOCKET": "/x.sock"}, extra_prompt="\n## Cluster jobs\n")
    args = loop._permission_args()
    assert args[-1] == "Bash(aurora job *)" and "Bash(docker exec abc *)" in args


async def test_await_ends_the_turn_and_resumes_when_jobs_end(tmp_path, slurm):
    """`aurora job await`: the agent's session ends, the job's clock stops, and the agent is resumed
    with the results when its cluster jobs end."""
    import asyncio
    import time

    from aurora.config import AuroraConfig
    from aurora.core.events import EventBus
    from aurora.core.terminal_iteration import TerminalIteration
    from aurora.models.experiment import ExperimentJob, ExperimentPlan
    fake, image = slurm
    m, _ = manager(tmp_path, image)
    await m.start()
    try:
        cfg = AuroraConfig(objective="x", agent={"research_timeout_minutes": 1})
        it = TerminalIteration(cfg, backend=None, event_bus=EventBus(), compute=m)
        sessions = []

        async def fake_session(message, time_left, resume_id):
            sessions.append((message, round(time_left), resume_id))
            if len(sessions) == 1:                     # the agent submits, awaits, ends its turn
                await m.submit("python train.py", name="train", gpus=1)
                r = await m.await_jobs(["1"], agent_job="j1")
                assert "end your turn" in r["message"]
                it.session_id = "sess-1"
                return {"turns": 3, "cost_usd": 0.1, "final_text": "waiting for job 1"}
            return {"turns": 2, "cost_usd": 0.05, "final_text": "trained; accuracy 0.93"}
        it._run_session = fake_session
        monkey_poll = __import__("aurora.core.terminal_iteration", fromlist=["x"])
        monkey_poll._CLUSTER_POLL_SECONDS = 0.05

        async def finish_job_later():
            await asyncio.sleep(1.5)                   # longer than... the job's own clock would allow
            (fake / "queue").unlink(missing_ok=True)
            (tmp_path / "campaign/workspace/.aurora/jobs/1-train/exit_code").write_text("0")
            await m._poll()
        (fake / "queue").write_text("4242|RUNNING|0:10|None|node1\n")
        await m._poll()
        finisher = asyncio.create_task(finish_job_later())
        start = time.time()
        job = ExperimentJob(job_id="j1", skill_name="train a model")
        result = await it.run(job, ExperimentPlan(plan_id="p", proposal_id="q", jobs=[job]))
        await finisher
        assert time.time() - start >= 1.5
        assert result.decision == "done" and result.job_result.summary == "trained; accuracy 0.93"
        assert len(sessions) == 2 and sessions[1][2] == "sess-1"          # the same session, resumed
        wake = sessions[1][0]
        assert "have ended" in wake and "COMPLETED (exit 0)" in wake and "/app/.aurora/jobs/1-train/slurm.out" in wake
        assert sessions[1][1] >= 55                    # the wait did not use up the job's minute
        assert result.job_result.metrics["turns"] == 5 and not m.awaiting("j1")
    finally:
        await m.stop()
        monkey_poll._CLUSTER_POLL_SECONDS = 5.0


async def test_a_researcher_message_wakes_a_waiting_agent(tmp_path, slurm):
    import aurora.core.terminal_iteration as ti
    from aurora.config import AuroraConfig
    from aurora.core.events import EventBus
    from aurora.core.terminal_iteration import TerminalIteration
    fake, image = slurm
    m, _ = manager(tmp_path, image)
    await m.start()
    ti._CLUSTER_POLL_SECONDS = 0.05
    try:
        await m.submit("python train.py", name="train")
        (fake / "queue").write_text("4242|RUNNING|0:10|None|node1\n")
        await m._poll()
        await m.await_jobs([1], agent_job="j1")
        it = TerminalIteration(AuroraConfig(objective="x"), backend=None, event_bus=EventBus(), compute=m)
        import asyncio
        waiting = asyncio.create_task(it._wait_for_cluster_jobs("j1"))
        await asyncio.sleep(0.2)
        assert not waiting.done() and it.paused
        await it.inject_message("use a smaller learning rate next time")
        wake = await asyncio.wait_for(waiting, 2)
        assert "The researcher wrote" in wake and "RUNNING" in wake
        assert "use a smaller learning rate" in wake and not it.paused
        assert m.check_ins["j1"] == 0                  # a message starts the check-ins over
    finally:
        ti._CLUSTER_POLL_SECONDS = 5.0
        await m.stop()


def test_agents_inside_the_container(tmp_path, slurm, monkeypatch):
    """They reach the socket through the mounted directory and get AURORA_* in their environment."""
    import asyncio

    from aurora.compute.slurm import CONTAINER_SOCKET_DIR
    from aurora.llm.agent_loops import ClaudeCodeLoop
    _, image = slurm
    m, _ = manager(tmp_path, image)
    assert m.agent_env(in_container=True) == {"AURORA_COMPUTE_SOCKET": f"{CONTAINER_SOCKET_DIR}/jobs.sock"}
    assert "plain command" in m.agent_prompt(in_container=True)
    seen = {}

    async def capture(*cmd, **kw):
        seen["cmd"] = cmd
        raise RuntimeError("stop here")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", capture)
    loop = ClaudeCodeLoop(cwd=str(tmp_path), container_agent={"container": "c1", "HOME": "/aurora/home"},
                          extra_env={**m.agent_env(in_container=True), "AURORA_AGENT_JOB": "j1", "PATH": "/host"})
    with pytest.raises(RuntimeError):
        asyncio.run(loop.run("sys", "go"))
    cmd = " ".join(seen["cmd"])
    assert "-e AURORA_COMPUTE_SOCKET=/aurora/run/jobs.sock" in cmd and "-e AURORA_AGENT_JOB=j1" in cmd
    assert "PATH=/host" not in cmd


async def test_a_failed_squeue_is_not_the_end_of_the_jobs(tmp_path, slurm):
    fake, image = slurm
    m, _ = manager(tmp_path, image)
    await m.start()
    try:
        await m.submit("python train.py", name="train")
        (fake / "queue").write_text("4242|RUNNING|0:10|None|node1\n")
        await m._poll()
        (fake / "squeue_fail").write_text("slurm_load_jobs error: Socket timed out on send/recv operation")
        await m._poll()
        assert m.jobs[0].state == "RUNNING"                     # no answer is not "it ended"
        assert "Socket timed out" in (await m.status())["slurm_error"]
        (fake / "squeue_fail").unlink()
        await m._poll()
        assert m.slurm_error == ""
        # a job SLURM has forgotten: squeue fails for it alone, and that means it ended
        (fake / "queue").unlink()
        (fake / "squeue_fail").write_text("slurm_load_jobs error: Invalid job id specified")
        (fake / "purged").write_text("")
        (tmp_path / "campaign/workspace/.aurora/jobs/1-train/exit_code").write_text("3")
        await m._poll()
        assert (m.jobs[0].state, m.jobs[0].exit_code) == ("FAILED", 3)
    finally:
        await m.stop()


async def test_check_ins_come_sooner_first_then_less_often(tmp_path, slurm):
    """A waiting agent is shown its jobs' state after each check-in interval (10 min at first, later up to
    an hour), whatever the state is; it decides what to do. Jobs that end wake it at once."""
    import asyncio

    import aurora.core.terminal_iteration as ti
    from aurora.config import AuroraConfig
    from aurora.core.events import EventBus
    fake, image = slurm
    m, _ = manager(tmp_path, image, check_in_minutes=[0.005, 0.01, 0.02])   # 0.3 s, 0.6 s, 1.2 s
    assert manager(tmp_path, image)[0].config.check_in_minutes == [10, 10, 10, 20, 30, 45, 60]
    await m.start()
    ti._CLUSTER_POLL_SECONDS = 0.02
    try:
        await m.submit("python train.py", name="train", time_limit="11:00:00")
        (fake / "queue").write_text("4242|PENDING|0:00|PartitionTimeLimit|\n")
        await m._poll()
        it = ti.TerminalIteration(AuroraConfig(objective="x"), backend=None, event_bus=EventBus(), compute=m)
        waited = []
        for _ in range(4):
            await m.await_jobs([1], agent_job="j1")
            t = asyncio.get_running_loop().time()
            wake = await it._wait_for_cluster_jobs("j1")
            waited.append(round(asyncio.get_running_loop().time() - t, 1))
        assert waited == [0.3, 0.6, 1.2, 1.2]                  # longer each time, the last repeating
        assert "Check-in after" in wake and "PENDING [PartitionTimeLimit]" in wake   # the agent sees why
        # the job ends: woken at once, and the check-ins start over
        await m.await_jobs([1], agent_job="j1")
        waiting = asyncio.create_task(it._wait_for_cluster_jobs("j1"))
        (fake / "queue").unlink()
        await m._poll()
        wake = await asyncio.wait_for(waiting, 1)
        assert "have ended" in wake and m.check_ins["j1"] == 0
    finally:
        ti._CLUSTER_POLL_SECONDS = 5.0
        await m.stop()
