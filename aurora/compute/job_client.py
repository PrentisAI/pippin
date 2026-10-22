"""``aurora job``: how a research agent submits and follows cluster jobs.

It sends one request to the experiment's job manager (:mod:`aurora.compute.slurm`) over the Unix
socket named by ``AURORA_COMPUTE_SOCKET`` and prints the answer.

This file uses the standard library only: it is also mounted into the experiment's container
(``/aurora/lib/aurora_job.py``), where agents that run inside the container call it.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import socket
import sys
import time
from typing import Any

MAX_WAIT_MINUTES = 9          # Claude Code's Bash tool gives up after 10 minutes


def request(payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    path = os.environ.get("AURORA_COMPUTE_SOCKET", "")
    if not path:
        return {"ok": False, "error": "cluster jobs are not enabled for this experiment"}
    # tells the manager which research job is asking (each runs its own agent)
    payload = {**payload, "agent_job": os.environ.get("AURORA_AGENT_JOB", "")}
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        try:
            s.connect(path)
        except OSError as exc:
            return {"ok": False, "error": f"the experiment's job manager is not reachable ({exc})"}
        s.sendall((json.dumps(payload) + "\n").encode())
        data = b""
        while not data.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            data += chunk
    return json.loads(data or b'{"ok": false, "error": "no answer"}')


def _age(seconds: float) -> str:
    seconds = int(max(0, seconds))
    h, rest = divmod(seconds, 3600)
    return f"{h}h{rest // 60:02d}m" if h else f"{rest // 60}m{rest % 60:02d}s"


def line(job: dict[str, Any]) -> str:
    """One job as a row of `aurora job status`."""
    state = job["state"]
    if job.get("exit_code") is not None:
        state += f" (exit {job['exit_code']})"
    if job.get("reason") and job["active"]:
        state += f" [{job['reason']}]"
    run = _age(job.get("elapsed_seconds", 0)) if job.get("elapsed_seconds") else "-"
    return (f"{job['id']:>3}  {job['name'][:20]:<20} {job['slurm_id'] or '-':>8}  {state:<28} "
            f"{job['gpus']} GPU  {run:>7}  {job['log']}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aurora job", description="Cluster jobs of this experiment.")
    jobs = parser.add_subparsers(dest="job_cmd", metavar="ACTION", required=True)
    s = jobs.add_parser("submit", help="submit a command as a cluster job")
    s.add_argument("--name", default="job")
    s.add_argument("--gpus", type=int, default=0)
    s.add_argument("--time", default="", help="time limit, HH:MM:SS (or D-HH:MM:SS)")
    s.add_argument("--partition", default="")
    s.add_argument("--cpus", type=int, default=None)
    s.add_argument("--mem", type=int, default=None, help="memory in GB")
    s.add_argument("command", nargs=argparse.REMAINDER, help="-- then the command")
    jobs.add_parser("status", help="list jobs").add_argument("job", nargs="?", default=None)
    lg = jobs.add_parser("logs", help="a job's output")
    lg.add_argument("job")
    lg.add_argument("--tail", type=int, default=200)
    jobs.add_parser("cancel", help="cancel a job").add_argument("job")
    w = jobs.add_parser("wait", help=f"wait for a job to end (at most {MAX_WAIT_MINUTES} minutes)")
    w.add_argument("job")
    w.add_argument("--minutes", type=float, default=5)
    a = jobs.add_parser("await", help="end your turn and be resumed when these jobs end")
    a.add_argument("jobs", nargs="+")
    return parser


def run(args: argparse.Namespace) -> int:
    op = args.job_cmd
    timeout = 60.0
    payload: dict[str, Any] = {"op": op}
    if op == "submit":
        words = args.command[1:] if args.command[:1] == ["--"] else args.command
        if not words:
            print("usage: aurora job submit [options] -- <command>", file=sys.stderr)
            return 2
        # one argument: a shell line as written ('a && b'); several: the words of one command
        payload.update(command=words[0] if len(words) == 1 else shlex.join(words), name=args.name,
                       gpus=args.gpus, time_limit=args.time or "", partition=args.partition or "",
                       cpus=args.cpus, mem_gb=args.mem)
        timeout = 900.0        # the first job may wait for the image to be saved for the nodes
    elif op in ("status", "cancel"):
        payload["job"] = args.job
    elif op == "logs":
        payload.update(job=args.job, tail=args.tail)
    elif op == "wait":
        minutes = min(args.minutes, MAX_WAIT_MINUTES)
        payload.update(job=args.job, minutes=minutes)
        timeout = minutes * 60 + 60
    elif op == "await":
        payload["jobs"] = args.jobs
    started = time.time()
    answer = request(payload, timeout)
    if not answer.get("ok"):
        print(f"error: {answer.get('error')}", file=sys.stderr)
        return 1
    if op == "status":
        jobs = answer.get("jobs", [])
        if not jobs:
            print("No cluster jobs yet.")
        else:
            ident = "pid" if answer.get("backend") == "SSH" else "slurm"
            print(f"{'id':>3}  {'name':<20} {ident:>8}  {'state':<28} gpus  {'ran':>7}  output")
            for job in jobs:
                print(line(job))
            print(f"GPU hours used: {answer.get('gpu_hours', 0)}")
        if answer.get("slurm_error"):
            where = "the GPU machine" if answer.get("backend") == "SSH" else "SLURM"
            print(f"Asking {where} failed ({answer['slurm_error']}); these states may be out of date.")
    elif op == "logs":
        print(answer.get("text") or answer.get("message", ""))
    elif op == "await":
        print(answer.get("message", ""))
        for job in answer.get("jobs", []):
            print(line(job))
    else:
        if op == "wait" and answer["job"]["active"]:
            print(f"(waited {_age(time.time() - started)}; for long jobs use `aurora job await {args.job}` "
                  "and end your turn instead)")
        print(answer.get("message", ""))
        print(line(answer["job"]))
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["job"]:           # `aurora job ...` in the container runs this file directly
        argv = argv[1:]
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
