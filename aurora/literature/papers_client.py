"""``aurora papers``: how a research agent searches the literature and checks references.

It sends one request to the experiment's paper service (:mod:`aurora.literature.agent_service`) over
the Unix socket named by ``AURORA_PAPERS_SOCKET`` and prints the answer.

This file uses the standard library only: it is also mounted into the experiment's container
(``/aurora/lib/aurora_papers.py``), where agents that run inside the container call it.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from typing import Any


def request(payload: dict[str, Any], timeout: float = 180.0) -> dict[str, Any]:
    path = os.environ.get("AURORA_PAPERS_SOCKET", "")
    if not path:
        return {"ok": False, "error": "searching the literature is not enabled for this experiment"}
    payload = {**payload, "agent_job": os.environ.get("AURORA_AGENT_JOB", ""),
               "app": os.environ.get("AURORA_PAPERS_APP", "")}     # where the workspace is, as the agent sees it
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        try:
            s.connect(path)
        except OSError as exc:
            return {"ok": False, "error": f"the experiment's paper service is not reachable ({exc})"}
        s.sendall((json.dumps(payload) + "\n").encode())
        data = b""
        while not data.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            data += chunk
    return json.loads(data or b'{"ok": false, "error": "no answer"}')


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aurora papers",
        description="Search the scholarly indexes, check references and read papers, for this experiment.")
    sub = parser.add_subparsers(dest="cmd", metavar="ACTION", required=True)
    s = sub.add_parser("search", help="search OpenAlex, arXiv, Crossref and Semantic Scholar")
    s.add_argument("query", nargs="+")
    s.add_argument("--limit", type=int, default=8)
    c = sub.add_parser("check", help="check references: a DOI, an arXiv ID, or a citation as written")
    c.add_argument("refs", nargs="*", help="each argument is one reference")
    c.add_argument("--file", help="check every reference in this file (a .bib file, or a text that cites papers)")
    sh = sub.add_parser("show", help="one paper in full: abstract, links, the researcher's highlights and notes")
    sh.add_argument("paper", help="a key such as Behrens12, a DOI or an arXiv ID")
    sub.add_parser("list", help="the papers in the project's literature, with their keys")
    b = sub.add_parser("bib", help="BibTeX for papers (all the project's papers without keys)")
    b.add_argument("keys", nargs="*")
    ci = sub.add_parser("cite", help="record that your work uses these papers (they join the literature)")
    ci.add_argument("keys", nargs="+")
    p = sub.add_parser("pdf", help="download a paper's PDF into the workspace (papers/<key>.pdf)")
    p.add_argument("paper")
    p.add_argument("--text", action="store_true", help="also extract its text (papers/<key>.txt)")
    return parser


def run(args: argparse.Namespace) -> int:
    payload: dict[str, Any] = {"op": args.cmd}
    if args.cmd == "search":
        payload.update(query=" ".join(args.query), limit=max(1, min(args.limit, 25)))
    elif args.cmd == "check":
        refs = list(args.refs)
        text = ""
        if args.file:
            try:
                with open(args.file, encoding="utf-8", errors="replace") as f:
                    text = f.read()
            except OSError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 1
        if not refs and not text:
            print("usage: aurora papers check <reference> [<reference> ...] | --file refs.bib", file=sys.stderr)
            return 2
        payload.update(refs=refs, text=text)
    elif args.cmd in ("show", "pdf"):
        payload["paper"] = args.paper
        payload["text"] = getattr(args, "text", False)
    elif args.cmd in ("bib", "cite"):
        payload["keys"] = args.keys
    answer = request(payload)
    if not answer.get("ok"):
        print(f"error: {answer.get('error')}", file=sys.stderr)
        return 1
    print(answer.get("text", "").rstrip())
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["papers"]:        # `aurora papers ...` in the container runs this file directly
        argv = argv[1:]
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
