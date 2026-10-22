"""``aurora results``: how a research agent records the numbers it will report, with their provenance.

It sends one request to the experiment's result service (:mod:`aurora.results.agent_service`) over the
Unix socket named by ``AURORA_RESULTS_SOCKET`` and prints the answer.

This file uses the standard library only: it is also mounted into the experiment's container
(``/aurora/lib/aurora_results.py``), where agents that run inside the container call it.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from typing import Any


def request(payload: dict[str, Any], timeout: float = 60.0) -> dict[str, Any]:
    path = os.environ.get("AURORA_RESULTS_SOCKET", "")
    if not path:
        return {"ok": False, "error": "recording results is not enabled for this experiment"}
    payload = {**payload, "agent_job": os.environ.get("AURORA_AGENT_JOB", "")}
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        try:
            s.connect(path)
        except OSError as exc:
            return {"ok": False, "error": f"the experiment's result service is not reachable ({exc})"}
        s.sendall((json.dumps(payload) + "\n").encode())
        data = b""
        while not data.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            data += chunk
    return json.loads(data or b'{"ok": false, "error": "no answer"}')


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aurora results",
                                     description="Record the results of this experiment and where they came from.")
    sub = parser.add_subparsers(dest="cmd", metavar="ACTION", required=True)
    r = sub.add_parser("record", help="record a result (a number, or a short categorical outcome)")
    r.add_argument("name", help="what it is, e.g. 'weekend demand elasticity'")
    r.add_argument("value")
    r.add_argument("--unit", default="")
    r.add_argument("--uncertainty", default=None, help="standard error, or half a confidence interval")
    r.add_argument("--script", default="", help="the script that computed it")
    r.add_argument("--command", default="", help="the command that ran it, as typed")
    r.add_argument("--inputs", nargs="*", default=[], help="files it read")
    r.add_argument("--outputs", nargs="*", default=[], help="files it wrote (tables, figures)")
    r.add_argument("--note", default="", help="one sentence on what it means or how it was computed")
    sub.add_parser("list", help="the results recorded so far")
    sh = sub.add_parser("show", help="one result and its provenance")
    sh.add_argument("id")
    return parser


def run(args: argparse.Namespace) -> int:
    payload: dict[str, Any] = {"op": args.cmd}
    if args.cmd == "record":
        payload.update(name=args.name, value=args.value, unit=args.unit, uncertainty=args.uncertainty,
                       script=args.script, command=args.command, inputs=args.inputs, outputs=args.outputs,
                       note=args.note, cwd=os.getcwd())
    elif args.cmd == "show":
        payload["id"] = args.id
    answer = request(payload)
    if not answer.get("ok"):
        print(f"error: {answer.get('error')}", file=sys.stderr)
        return 1
    print(answer.get("text", "").rstrip())
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["results"]:       # `aurora results ...` in the container runs this file directly
        argv = argv[1:]
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
