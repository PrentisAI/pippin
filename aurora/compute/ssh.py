"""Jobs on a GPU machine reached over SSH: for a lab server or a cloud VM without a scheduler.

The agents use the same ``aurora job`` commands as on a SLURM cluster; this manager keeps the same
records and limits (:class:`aurora.compute.slurm.SlurmCompute`) and replaces the scheduler:

* before a job starts, the experiment's workspace is copied to the machine (rsync, only what
  changed; files newer there are kept), and when it ends the workspace is copied back;
* a job gets GPUs that no job of this console holds and that are not busy (their memory use is at
  most ``busy_memory_mb``); when there are not enough it waits, like a queued job;
* it runs in Docker there (the experiment's image, sent over once when the machine does not have
  it, with only its GPUs visible) or, with ``docker: false``, directly in the copied workspace with
  ``CUDA_VISIBLE_DEVICES`` set;
* its time limit is kept with ``timeout``; cancelling stops its session (and its container).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import shlex
import time
from pathlib import Path
from typing import Any

from aurora.compute.job_client import MAX_WAIT_MINUTES
from aurora.compute.slurm import GPU_QUERY, ClusterJob, SlurmCompute, parse_gpus, parse_time
from aurora.config import SSHComputeConfig

logger = logging.getLogger(__name__)

# GPUs held by the running jobs of this console, by machine: {host: {gpu index: (experiment tag, job id)}}
_CLAIMED: dict[str, dict[int, tuple[str, int]]] = {}


class SSHCompute(SlurmCompute):
    """The jobs of one experiment on one GPU machine (see the module's description)."""

    kind = "SSH"
    config: SSHComputeConfig          # type: ignore[assignment]

    def __init__(self, config: SSHComputeConfig, **kwargs: Any) -> None:
        super().__init__(config, **kwargs)   # type: ignore[arg-type]
        base = config.remote_dir.strip() or "aurora-jobs"
        base = base[2:] if base.startswith("~/") else base
        self.remote = f"{base.rstrip('/')}/{self.tag}"
        self._images: dict[int, str] = {}         # job -> the image it runs in
        claimed = _CLAIMED.setdefault(config.host, {})
        for job in self.jobs:              # jobs of an earlier run of this experiment, still running
            if job.active and job.gpu_ids:
                for g in job.gpu_ids.split(","):
                    claimed[int(g)] = (self.tag, job.id)

    # ------------------------------------------------------------------ what the agents get

    def agent_prompt(self, in_container: bool = False) -> str:
        c = self.config
        limits = [f"at most {c.max_gpus_per_job} GPUs and {c.max_time} per job", f"{c.max_jobs} jobs at a time"]
        if c.max_gpu_hours:
            limits.append(f"{c.max_gpu_hours:g} GPU hours for the whole experiment")
        where = ("It runs this container's image there, in /app, with a copy of this workspace as /app."
                 if c.docker else
                 "It runs directly on that machine (not in this container's image), in a copy of this "
                 "workspace: use paths relative to the workspace, not /app, and set up its packages in "
                 "the job's command (or a requirements file it installs).")
        return (
            "\n## GPU jobs (a GPU machine over SSH)\n"
            "The container has no GPU. For training, GPU work or anything that runs long, submit a "
            "job; keep the container for writing code and quick tests on small inputs:\n"
            "  aurora job submit --name train --gpus 1 --time 2:00:00 -- python train.py --epochs 20\n"
            f"The job runs on a GPU machine. {where} The workspace is copied there when the job starts "
            "and copied back when it ends (files it wrote appear here then; the output is shown live by "
            "`aurora job logs`). Its output goes to /app/.aurora/jobs/<id>-<name>/job.out.\n"
            "  aurora job status [<id>]            the jobs and their states\n"
            "  aurora job logs <id> [--tail 200]   its output\n"
            "  aurora job cancel <id>\n"
            "  aurora job await <id> [<id> ...]    then END YOUR TURN: you are resumed when they end\n"
            f"  aurora job wait <id> [--minutes N]   wait in place for a short job (at most {MAX_WAIT_MINUTES} min)\n"
            "To wait for a job that takes longer than a few minutes, run `aurora job await <ids>` and end "
            "your turn with a short note of what you are waiting for and what you will do next. You are "
            f"resumed when the jobs end, and in between (after {c.check_in_minutes[0]:g} minutes at first, later "
            f"every {c.check_in_minutes[-1]:g} minutes) with their state. A job waits when the GPUs it asks for "
            "are in use; its reason says so. Do not poll with sleep.\n"
            + ("Run `aurora job` as a plain command. " if in_container
               else "Run `aurora job` directly, not inside docker exec. ")
            + f"Options: --gpus, --time (HH:MM:SS){', --cpus, --mem (GB)' if c.docker else ''}. "
            f"Limits: {'; '.join(limits)}. Jobs keep running when your session ends (unless the "
            "experiment ends): name the job IDs and their output in your report.\n"
        )

    # ------------------------------------------------------------------ the machine

    def _ssh(self) -> list[str]:
        return ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-o", "ServerAliveInterval=30",
                *self.config.ssh_options, self.config.host]

    def _argv(self, script: str) -> list[str]:
        """The command that runs *script* on the machine (a login shell: conda, CUDA and docker on PATH)."""
        return [*self._ssh(), "bash -lc " + shlex.quote(script)]

    def _rsync_target(self, path: str) -> str:
        return f"{self.config.host}:{path}"

    def _rsync_shell(self) -> list[str]:
        return ["-e", shlex.join(["ssh", "-o", "BatchMode=yes", *self.config.ssh_options])]

    async def _sh(self, script: str, timeout: float = 60.0) -> tuple[int, str, str]:
        proc = await asyncio.create_subprocess_exec(*self._argv(script), stdin=asyncio.subprocess.DEVNULL,
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout)
        except TimeoutError:
            proc.kill()
            return 124, "", f"{self.config.host} did not answer in {timeout:g} s"
        return proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")

    async def _rsync(self, *, up: bool, timeout: float = 3600.0) -> None:
        local = str(self.workspace_dir) + "/"
        remote = self._rsync_target(self.remote + "/")
        excludes = [f"--exclude={p}" for p in self.config.sync_exclude]
        # -u: a file newer on the receiving side is kept (a job's output, or the agent's latest edit)
        args = ["rsync", "-a", "-z", "-u", *excludes, *self._rsync_shell(),
                *((local, remote) if up else (remote, local))]
        proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            _, err = await asyncio.wait_for(proc.communicate(), timeout)
        except TimeoutError:
            proc.kill()
            raise RuntimeError("copying the workspace timed out") from None
        # 24: files vanished while copying (a job writing): what was there is copied
        if proc.returncode not in (0, 24):
            raise RuntimeError(f"rsync {'to' if up else 'from'} {self.config.host} failed: "
                               f"{err.decode(errors='replace').strip()[-400:]}")

    async def _query_gpus(self) -> list[dict[str, Any]]:
        code, out, err = await self._sh(f"nvidia-smi --query-gpu={GPU_QUERY} --format=csv,noheader,nounits", 30)
        if code != 0:
            raise RuntimeError((err or out).strip()[-300:] or "nvidia-smi failed")
        return parse_gpus(out)

    # ------------------------------------------------------------------ jobs

    def _partition(self, partition: str) -> str:
        if partition:
            raise ValueError("this GPU machine has no partitions: leave out --partition")
        return ""

    def _new_job(self, job: ClusterJob) -> None:
        job.backend, job.out, job.node = "ssh", "job.out", self.config.host

    def _submitted(self, job: ClusterJob) -> str:
        if job.state == "RUNNING":
            start = f"started on {self.config.host} with GPU {job.gpu_ids}" if job.gpu_ids else f"started on {self.config.host}"
        else:
            start = f"waiting: {job.reason}"
        return (f"Submitted job {job.id} ({job.name}): {job.gpus} GPU(s), up to {job.time_limit}; {start}. "
                f"Output: /app/{job.dir}/{job.out}")

    def _following(self) -> bool:
        return any(j.active for j in self.jobs)

    async def _sbatch(self, job: ClusterJob, image: str) -> str:
        """Write the job's scripts and start it if GPUs are free (else it waits); returns its process ID or ''."""
        job_dir = self.workspace_dir / job.dir
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / "run.sh").write_text(f"#!/bin/bash\n# job {job.id} ({job.name})\n{job.command}\n")
        self._images[job.id] = image
        async with self._lock:
            started = await self._try_start(job)
        return job.slurm_id if started else ""

    async def _try_start(self, job: ClusterJob) -> bool:
        claimed = _CLAIMED.setdefault(self.config.host, {})
        gpus: list[int] = []
        if job.gpus:
            try:
                info = await self._query_gpus()
            except RuntimeError as exc:
                self.slurm_error = f"{time.strftime('%H:%M')}: {exc}"
                job.reason = f"waiting: could not ask {self.config.host} about its GPUs"
                return False
            allowed = set(self.config.gpus) if self.config.gpus else {g["index"] for g in info}
            free = [g["index"] for g in info if g["index"] in allowed and g["index"] not in claimed
                    and (g["mem_used_mb"] or 0) <= self.config.busy_memory_mb]
            if len(free) < job.gpus:
                reason = f"waiting for {job.gpus} free GPU(s) ({len(free)} free now of {len(allowed)})"
                if job.gpus > len(allowed):
                    reason = f"cannot start: {job.gpus} GPUs asked, the machine lets jobs use {len(allowed)}"
                job.reason = reason
                return False
            gpus = free[:job.gpus]
        job.gpu_ids = ",".join(map(str, gpus))
        for g in gpus:
            claimed[g] = (self.tag, job.id)
        try:
            self._write_launch(job, self._images.get(job.id, ""))
            await self._make_writable(job)
            q = shlex.quote
            code, out, err = await self._sh(f"mkdir -p {q(self.remote)}", 60)
            if code != 0:
                raise RuntimeError((err or out).strip()[-400:] or f"could not reach {self.config.host}")
            await self._rsync(up=True)
            code, out, err = await self._sh(
                # its own session (setsid): cancelling stops everything in it, timeout's process group too
                f"cd {q(self.remote)} || exit 1; setsid nohup bash {q(job.dir)}/launch.sh > {q(job.dir)}/{job.out} "
                "2>&1 < /dev/null & echo $!", 60)
            pid = out.strip().splitlines()[-1] if out.strip() else ""
            if code != 0 or not pid.isdigit():
                raise RuntimeError((err or out).strip()[-400:] or f"starting the job failed ({code})")
        except Exception:
            self._release(job)
            job.gpu_ids = ""
            raise
        self.slurm_error = ""
        job.slurm_id, job.state, job.reason = pid, "RUNNING", ""
        job.started_at = time.time()
        return True

    def _write_launch(self, job: ClusterJob, image: str) -> None:
        c = self.config
        q = shlex.quote
        seconds = parse_time(job.time_limit)
        setup = c.setup.strip()
        if c.docker:
            inner = (f"{setup}\n" if setup else "") + f"timeout -k 60 {seconds} bash /app/{job.dir}/run.sh"
            device = '"device=' + job.gpu_ids + '"'
            body = " ".join([
                "docker run --rm", f"--name={q(job.job_name)}", *([f"--gpus={q(device)}"] if job.gpu_ids else []),
                f"--cpus={job.cpus}", f"--memory={job.mem_gb}g", "--shm-size=8g", "-e HOME=/tmp",
                '--user "$(id -u):$(id -g)"', '-v "$PWD":/app', "-w /app", q(image), "bash -c", q(inner)])
        else:
            body = ((f"{setup}\n" if setup else "")
                    + (f"export CUDA_VISIBLE_DEVICES={job.gpu_ids}\n" if job.gpus else "export CUDA_VISIBLE_DEVICES=\n")
                    + f"timeout -k 60 {seconds} bash {q(job.dir)}/run.sh")
        (self.workspace_dir / job.dir / "launch.sh").write_text(
            "#!/bin/bash\n# started by Pippin; the job's command is in run.sh\n"
            f'cd "$(dirname "$0")/{"/".join([".."] * len(Path(job.dir).parts))}"\n'
            f"{body}\ncode=$?\necho $code > {q(job.dir)}/exit_code\nexit $code\n")

    def _release(self, job: ClusterJob) -> None:
        claimed = _CLAIMED.get(self.config.host, {})
        for g, holder in list(claimed.items()):
            if holder == (self.tag, job.id):
                del claimed[g]

    async def _cancel(self, job: ClusterJob, reason: str) -> None:
        if not job.slurm_id:                       # still waiting for GPUs: it never started
            job.state, job.reason, job.ended_at = "CANCELLED", reason, time.time()
            self._save()
            await self._emit("compute_job_ended", job)
            return
        stop = f"docker stop -t 20 {shlex.quote(job.job_name)} >/dev/null 2>&1; " if self.config.docker else ""
        sid = int(job.slurm_id)
        code, out, err = await self._sh(
            f"{stop}pkill -TERM -s {sid}; for i in $(seq 15); do pgrep -s {sid} >/dev/null || break; sleep 1; done; "
            f"pkill -KILL -s {sid}; true", 90)
        if code != 0:
            raise RuntimeError((err or out).strip()[-300:] or f"could not reach {self.config.host}")
        job.reason = reason
        self._save()
        self._wake()

    async def stop(self) -> None:
        stopped = []
        if self.config.cancel_on_finish:
            for job in [j for j in self.jobs if j.active]:
                if job.slurm_id:
                    stopped.append(job)
                else:
                    job.state, job.reason, job.ended_at = "CANCELLED", "the experiment ended", time.time()
        await super().stop()
        if stopped:
            for job in stopped:
                job.state, job.ended_at = "CANCELLED", time.time()
            with contextlib.suppress(Exception):      # what they wrote until then
                await self._rsync(up=False, timeout=600)
            self._save()
        for job in self.jobs:
            self._release(job)

    async def _poll(self) -> None:
        running = [j for j in self.jobs if j.active and j.slurm_id]
        if running:
            q = shlex.quote
            checks = "; ".join(
                f"if [ -f {q(j.dir)}/exit_code ]; then echo \"{j.id}|done|$(cat {q(j.dir)}/exit_code)\"; "
                f"elif kill -0 {int(j.slurm_id)} 2>/dev/null; then echo '{j.id}|running|'; else echo '{j.id}|gone|'; fi"
                for j in running)
            code, out, err = await self._sh(f"cd {q(self.remote)} && {{ {checks}; }}", 60)
            if code != 0:
                error = (err or out).strip()[-300:] or f"ssh exited with {code}"
                if not self.slurm_error:
                    logger.warning("Could not ask %s about the jobs: %s", self.config.host, error)
                self.slurm_error = f"{time.strftime('%H:%M')}: {error}"
            else:
                self.slurm_error = ""
                states = {}
                for line in out.splitlines():
                    parts = line.strip().split("|")
                    if len(parts) == 3 and parts[0].isdigit():
                        states[int(parts[0])] = (parts[1], parts[2])
                for job in running:
                    what, value = states.get(job.id, ("running", ""))
                    if job.started_at:
                        job.elapsed_seconds = int(time.time() - job.started_at)
                    if what != "running":
                        exit_code = int(value) if value.strip().lstrip("-").isdigit() else None
                        await self._ended(job, exit_code)
        # jobs waiting for GPUs, in the order they were submitted
        for job in [j for j in self.jobs if j.active and not j.slurm_id]:
            async with self._lock:
                before = job.reason
                try:
                    started = await self._try_start(job)
                except Exception as exc:
                    job.state, job.reason, job.ended_at = "FAILED", f"could not start: {exc}", time.time()
                    await self._emit("compute_job_ended", job)
                    continue
            if started:
                await self._emit("compute_job_update", job)
            elif job.reason != before:
                await self._emit("compute_job_update", job)
        self._save()

    async def _ended(self, job: ClusterJob, exit_code: int | None) -> None:
        if exit_code is None:
            state = "CANCELLED" if job.reason else "FAILED"
            job.reason = job.reason or "the process ended without an exit code (killed, or the machine restarted)"
        elif exit_code == 0:
            state = "COMPLETED"
        elif exit_code == 124:
            state, job.reason = "TIMEOUT", job.reason or f"ran out of its time limit ({job.time_limit})"
        else:
            state = "CANCELLED" if job.reason else "FAILED"
        try:
            await self._make_writable(job)
            await self._rsync(up=False)
        except Exception as exc:
            job.reason = (job.reason + "; " if job.reason else "") + f"its files were not copied back: {exc}"
        self._release(job)
        job.state, job.exit_code, job.ended_at = state, exit_code, time.time()
        await self._emit("compute_job_ended", job)

    async def logs(self, job: Any, tail: int = 200) -> dict[str, Any]:
        j = self._job(job)
        if not (j.active and j.slurm_id):
            return await super().logs(job, tail)
        tail = max(1, min(int(tail), 2000))
        code, out, err = await self._sh(f"tail -n {tail} {shlex.quote(self.remote + '/' + j.dir + '/' + j.out)}", 30)
        if code != 0:
            return {"job": j.view(), "text": "", "message": (err or out).strip()[-300:] or "no output yet"}
        return {"job": j.view(), "text": out.rstrip("\n"), "lines": None}

    # ------------------------------------------------------------------ GPUs and the image

    async def gpu_usage(self) -> dict[str, Any]:
        """Every GPU jobs may use on the machine: what it is doing, and which job holds it."""
        try:
            info = await self._query_gpus()
        except RuntimeError as exc:
            return {"backend": self.kind, "host": self.config.host, "gpus": [], "errors": [str(exc)], "at": time.time()}
        allowed = set(self.config.gpus) if self.config.gpus else {g["index"] for g in info}
        claimed = _CLAIMED.get(self.config.host, {})
        names = {j.id: j.name for j in self.jobs}
        out = []
        for g in info:
            if g["index"] not in allowed:
                continue
            holder = claimed.get(g["index"])
            mine = holder is not None and holder[0] == self.tag
            out.append({**g, "node": self.config.host,
                        "jobs": [{"id": holder[1], "name": names.get(holder[1], "")}] if mine else [],
                        "other_experiment": holder is not None and not mine,
                        "busy": holder is None and (g["mem_used_mb"] or 0) > self.config.busy_memory_mb})
        return {"backend": self.kind, "host": self.config.host, "gpus": out, "errors": [], "at": time.time()}

    async def _image(self) -> str:
        if self._image_task is None or (self._image_task.done() and self._image_task.exception() is not None):
            self._image_task = asyncio.create_task(self._prepare_image())
        return await asyncio.shield(self._image_task)

    async def _prepare_image(self) -> str:
        """The image jobs run in, on the machine: there already, pulled there, or sent over once."""
        if not self.config.docker:
            return ""
        image = self.config.image or self.image
        q = shlex.quote
        code, _, _ = await self._sh(f"docker image inspect {q(image)} >/dev/null", 60)
        if code == 0:
            return image
        code, _, _ = await self._sh(f"docker pull {q(image)}", 1800)
        if code == 0:
            return image
        logger.info("Sending image %s to %s", image, self.config.host)
        load = shlex.join(self._argv("gunzip | docker load"))
        proc = await asyncio.create_subprocess_shell(
            f"set -o pipefail; docker save {q(image)} | gzip -1 | {load}", executable="/bin/bash",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        out, _ = await proc.communicate()
        if proc.returncode:
            raise RuntimeError(f"could not get image {image} onto {self.config.host}: "
                               f"{out.decode(errors='replace').strip()[-400:]}")
        return image
