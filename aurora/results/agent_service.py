"""What ``aurora results`` talks to: the result ledger of one experiment, for its agents."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import posixpath
import sys
from pathlib import Path
from typing import Any

from aurora.results import results_client
from aurora.results.ledger import ResultsLedger

logger = logging.getLogger(__name__)

CLIENT_PATH = Path(results_client.__file__).resolve()
CONTAINER_CLIENT = "/aurora/lib/aurora_results.py"
SOCKET_NAME = "results.sock"


class ResultService:
    allowed_tools = ["Bash(aurora results *)"]

    def __init__(self, ledger: ResultsLedger, *, socket_dir: Path, publish: Any = None) -> None:
        self.ledger = ledger
        self.socket_path = Path(socket_dir) / SOCKET_NAME
        self._publish = publish
        self._server: asyncio.base_events.Server | None = None

    async def start(self) -> None:
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(FileNotFoundError):
            self.socket_path.unlink()
        self._server = await asyncio.start_unix_server(self._handle, path=str(self.socket_path))
        os.chmod(self.socket_path, 0o600)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            with contextlib.suppress(Exception):
                await self._server.wait_closed()
        with contextlib.suppress(FileNotFoundError):
            self.socket_path.unlink()

    def agent_env(self, in_container: bool = False) -> dict[str, str]:
        if in_container:
            from aurora.compute.slurm import CONTAINER_SOCKET_DIR
            return {"AURORA_RESULTS_SOCKET": f"{CONTAINER_SOCKET_DIR}/{SOCKET_NAME}"}
        bin_dir = str(Path(sys.executable).parent)
        return {"AURORA_RESULTS_SOCKET": str(self.socket_path),
                "PATH": bin_dir + os.pathsep + os.environ.get("PATH", "/usr/bin:/bin")}

    def agent_prompt(self, in_container: bool = False) -> str:
        return (
            "\n## Recording results (`aurora results`)\n"
            "Every number you will report (a metric, an estimate, a count, a test statistic) must be recorded "
            "the moment you compute it, with what produced it:\n"
            "  aurora results record \"<what it is>\" <value> [--unit u] [--uncertainty se] --script <path> "
            "--command \"<how you ran it>\" [--inputs <files>] [--outputs <files>] [--note \"<one sentence>\"]\n"
            "  aurora results list          the results so far, with their IDs\n"
            "  aurora results show R3       one result and its provenance\n"
            "Each result gets an ID such as R3. In your summary and in any report, cite every number with its ID "
            "right after it, e.g. \"the elasticity is -0.42 {R3}\". A number without an ID cannot be traced and "
            "will be treated as unsupported. Record a result again under the same name when you recompute it; the "
            "new ID supersedes the old one.\n"
            + ("Run `aurora results` as a plain command.\n" if in_container
               else "Run `aurora results` directly, not inside docker exec.\n")
        )

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            req = json.loads(await reader.readline() or b"{}")
            handler = {"record": self.record, "list": self.list, "show": self.show}.get(req.get("op", ""))
            answer: dict[str, Any] = ({"ok": False, "error": f"unknown action {req.get('op')!r}"} if handler is None
                                      else {"ok": True, "text": await handler(req)})
        except ValueError as exc:
            answer = {"ok": False, "error": str(exc)}
        except Exception as exc:
            logger.warning("aurora results failed", exc_info=True)
            answer = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        try:
            writer.write((json.dumps(answer) + "\n").encode())
            await writer.drain()
        finally:
            writer.close()

    @staticmethod
    def _paths(req: dict[str, Any], key: str) -> list[str]:
        cwd = str(req.get("cwd") or "")
        out = []
        for p in req.get(key) or []:
            p = str(p).strip()
            if p and not p.startswith("/") and cwd:
                p = posixpath.join(cwd, p)
            if p:
                out.append(p)
        return out

    async def record(self, req: dict[str, Any]) -> str:
        script = self._paths({"cwd": req.get("cwd"), "s": [req.get("script")] if req.get("script") else []}, "s")
        r = self.ledger.record(req.get("name", ""), req.get("value"), unit=str(req.get("unit") or ""),
                               uncertainty=req.get("uncertainty"), description=str(req.get("note") or ""),
                               script=script[0] if script else "", command=str(req.get("command") or ""),
                               inputs=self._paths(req, "inputs"), outputs=self._paths(req, "outputs"),
                               job=str(req.get("agent_job") or ""), by="Research agent")
        if self._publish is not None:
            with contextlib.suppress(Exception):
                await self._publish("result_recorded", id=r["id"], name=r["name"], value=r["value"],
                                    unit=r["unit"], job=r["job"], supersedes=r.get("supersedes", ""))
        warn = []
        if r.get("script") and not r["script"].get("exists"):
            warn.append(f"the script {r['script']['path']} was not found in the workspace")
        missing = [f["path"] for f in r["inputs"] + r["outputs"] if not f.get("exists")]
        if missing:
            warn.append("not found: " + ", ".join(missing[:5]))
        if not r.get("script") and not r.get("command"):
            warn.append("no --script or --command: say what produced it, or it cannot be traced")
        return (f"Recorded {r['id']}: {r['name']} = {ResultsLedger.show_value(r)}"
                + (f" (supersedes {r['supersedes']})" if r.get("supersedes") else "")
                + f". Cite it as {{{r['id']}}}." + (f"\nWarning: {'; '.join(warn)}." if warn else ""))

    async def list(self, req: dict[str, Any]) -> str:
        return self.ledger.table() or "No results recorded yet."

    async def show(self, req: dict[str, Any]) -> str:
        r = self.ledger.get(str(req.get("id", "")))
        if r is None:
            raise ValueError(f"no result {req.get('id')!r}; see `aurora results list`")
        lines = [f"{r['id']}: {r['name']} = {ResultsLedger.show_value(r)}"]
        if r.get("description"):
            lines.append(r["description"])
        if r.get("script"):
            lines.append(f"script: {r['script']['path']}" + (" (copied)" if r["script"].get("snapshot") else ""))
        if r.get("command"):
            lines.append(f"command: {r['command']}")
        for k in ("inputs", "outputs"):
            if r.get(k):
                lines.append(f"{k}: " + ", ".join(f["path"] for f in r[k]))
        if r.get("job"):
            lines.append(f"job: {r['job']}")
        return "\n".join(lines)
