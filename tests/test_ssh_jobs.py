"""GPU jobs over SSH: GPU allocation, copying the workspace both ways, time limits, cancelling —
against a "remote machine" that is a local directory (commands run with bash, rsync copies locally)."""

import asyncio

import pytest

from aurora.compute import ssh as ssh_mod
from aurora.compute.ssh import SSHCompute
from aurora.config import ComputeConfig, SlurmConfig, SSHComputeConfig


class LocalMachine(SSHCompute):
    """The machine is this one: no ssh, and nvidia-smi answers from ``fake_gpus``."""

    fake_gpus: list[dict] = []

    def _argv(self, script):
        return ["bash", "-c", script]

    def _rsync_target(self, path):
        return path

    def _rsync_shell(self):
        return []

    async def _query_gpus(self):
        return [dict(g) for g in self.fake_gpus]


def gpu(i, used=0):
    return {"index": i, "name": "H200", "util": 0.0, "mem_used_mb": float(used), "mem_total_mb": 141000.0, "uuid": ""}


@pytest.fixture(autouse=True)
def no_claims():
    ssh_mod._CLAIMED.clear()
    yield
    ssh_mod._CLAIMED.clear()


def manager(tmp_path, gpus, **cfg):
    config = SSHComputeConfig(enabled=True, host="gpu-box", remote_dir=str(tmp_path / "remote"), docker=False,
                              max_gpus_per_job=2, max_jobs=3, **cfg)
    campaign = tmp_path / "campaign"
    (campaign / "workspace").mkdir(parents=True, exist_ok=True)
    events = []

    async def publish(topic, **data):
        events.append((topic, data))
    m = LocalMachine(config, campaign_dir=campaign, workspace_dir=campaign / "workspace", image="img",
                     publish=publish)
    m.fake_gpus = gpus
    return m, events, campaign / "workspace"


async def until_ended(m, job_id, rounds=100):
    for _ in range(rounds):
        await m._poll()
        if not m._job(job_id).active:
            return m._job(job_id)
        await asyncio.sleep(0.1)
    raise AssertionError(f"job {job_id} did not end: {m._job(job_id)}")


def test_config():
    with pytest.raises(ValueError, match="host"):
        SSHComputeConfig(enabled=True)
    with pytest.raises(ValueError, match="not both"):
        ComputeConfig(slurm=SlurmConfig(enabled=True), ssh=SSHComputeConfig(enabled=True, host="x"))
    assert ComputeConfig(ssh=SSHComputeConfig(enabled=True, host="x")).enabled


async def test_a_job_gets_a_free_gpu_runs_there_and_its_files_come_back(tmp_path):
    m, events, ws = manager(tmp_path, [gpu(0, used=30000), gpu(1)])     # GPU 0 is someone else's
    await m.start()
    m._poller.cancel()
    try:
        (ws / "train.py").write_text("print('hi')\n")
        with pytest.raises(ValueError, match="no partitions"):
            await m.submit("python train.py", partition="gpu")
        r = await m.submit("echo $CUDA_VISIBLE_DEVICES > gpus.txt; cat train.py > seen.txt; echo finished",
                           name="train", gpus=1)
        job = r["job"]
        assert job["state"] == "RUNNING" and job["gpu_ids"] == "1" and job["backend"] == "ssh"
        assert job["log"] == "/app/.aurora/jobs/1-train/job.out" and "GPU 1" in r["message"]
        assert (tmp_path / "remote" / m.tag / "train.py").exists()              # the workspace was copied there
        done = await until_ended(m, 1)
        assert done.state == "COMPLETED" and done.exit_code == 0
        assert (ws / "gpus.txt").read_text().strip() == "1"                   # its files came back
        assert (ws / "seen.txt").read_text() == "print('hi')\n"
        assert "finished" in (await m.logs(1))["text"]
        assert ssh_mod._CLAIMED["gpu-box"] == {}                               # the GPU is free again
        assert [e for e, _ in events] == ["compute_job_submitted", "compute_job_ended"]
    finally:
        await m.stop()


async def test_a_job_waits_for_gpus_then_starts(tmp_path):
    m, events, ws = manager(tmp_path, [gpu(0), gpu(1, used=50000)])
    await m.start()
    m._poller.cancel()
    try:
        r = await m.submit("echo two > two.txt", name="big", gpus=2)
        assert r["job"]["state"] == "PENDING" and r["job"]["slurm_id"] == ""
        assert "waiting for 2 free GPU(s) (1 free now of 2)" in r["message"]
        await m._poll()
        assert m._job(1).state == "PENDING"
        m.fake_gpus = [gpu(0), gpu(1)]                                          # the other user finished
        await m._poll()
        assert m._job(1).state == "RUNNING" and m._job(1).gpu_ids == "0,1"
        assert (await until_ended(m, 1)).state == "COMPLETED"
        assert (ws / "two.txt").read_text() == "two\n"
    finally:
        await m.stop()


async def test_jobs_of_one_console_do_not_share_gpus(tmp_path):
    m, _, _ = manager(tmp_path, [gpu(0)])
    await m.start()
    m._poller.cancel()
    try:
        assert (await m.submit("sleep 30", name="a", gpus=1))["job"]["state"] == "RUNNING"
        r = await m.submit("echo b", name="b", gpus=1)
        assert r["job"]["state"] == "PENDING"                                  # GPU 0 is job a's, though idle
        usage = await m.gpu_usage()
        assert usage["gpus"][0]["jobs"] == [{"id": 1, "name": "a"}]
        await m.cancel(1)
        assert (await until_ended(m, 1)).state == "CANCELLED"
        await m._poll()
        assert m._job(2).state in ("RUNNING", "COMPLETED")
    finally:
        await m.stop()


async def test_time_limit_and_cancel_on_finish(tmp_path):
    m, _, _ = manager(tmp_path, [gpu(0)])
    await m.start()
    m._poller.cancel()
    try:
        await m.submit("sleep 30", name="slow", time_limit="00:00:01")
        done = await until_ended(m, 1)
        assert done.state == "TIMEOUT" and done.exit_code == 124 and "time limit" in done.reason
        await m.submit("sleep 30", name="left")
        await m.submit("sleep 30", name="queued", gpus=1)
        await m.submit("sleep 30", name="queued2", gpus=1)
        assert m._job(4).state == "PENDING"
    finally:
        await m.stop()
    assert [m._job(i).state for i in (2, 3, 4)] == ["CANCELLED", "CANCELLED", "CANCELLED"]


def test_launch_in_docker(tmp_path):
    m, _, ws = manager(tmp_path, [], setup="pip install -q -r requirements.txt")
    m.config.docker = True
    from aurora.compute.slurm import ClusterJob
    job = ClusterJob(id=1, name="t", command="python t.py", gpus=2, cpus=16, mem_gb=128, time_limit="02:00:00",
                     partition="", dir=".aurora/jobs/1-t", job_name="aurora-x-1-t", gpu_ids="2,3")
    (ws / job.dir).mkdir(parents=True)
    m._write_launch(job, "lab/image:1")
    text = (ws / job.dir / "launch.sh").read_text()
    assert "docker run --rm --name=aurora-x-1-t --gpus='\"device=2,3\"' --cpus=16 --memory=128g" in text
    assert "lab/image:1 bash -c" in text and "timeout -k 60 7200 bash /app/.aurora/jobs/1-t/run.sh" in text
    assert "pip install -q -r requirements.txt" in text and 'cd "$(dirname "$0")/../../.."' in text
    assert "/app" in m.agent_prompt() and "--cpus" in m.agent_prompt()
    m.config.docker = False
    assert "not /app" in m.agent_prompt()
