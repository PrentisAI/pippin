"""Normalize AutoResearchEval trajectories (Claude Code / Gemini CLI / Codex) into one step list."""

from __future__ import annotations

import json
import re
from pathlib import Path


def _claude_code(rows: list[dict]) -> list[dict]:
    steps, names = [], {}
    for r in rows:
        content = (r.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        for b in content:
            t = b.get("type")
            if t == "text" and r.get("type") == "assistant" and b.get("text", "").strip():
                steps.append({"kind": "text", "tool": "", "body": b["text"]})
            elif t == "thinking" and b.get("thinking", "").strip():
                steps.append({"kind": "thinking", "tool": "", "body": b["thinking"]})
            elif t == "tool_use":
                names[b.get("id")] = b.get("name", "")
                steps.append({"kind": "call", "tool": b.get("name", ""),
                              "body": json.dumps(b.get("input", {}), ensure_ascii=False)})
            elif t == "tool_result":
                c = b.get("content")
                body = c if isinstance(c, str) else "\n".join(
                    x.get("text", "") if isinstance(x, dict) else str(x) for x in (c or []))
                steps.append({"kind": "output", "tool": names.get(b.get("tool_use_id"), ""), "body": body})
    return steps


def _gemini(rows: list[dict]) -> list[dict]:
    steps, last_tool = [], ""
    for r in rows:
        if r.get("type") == "action":
            last_tool = r.get("tool", "")
            steps.append({"kind": "call", "tool": last_tool, "body": r.get("content", "")})
        elif r.get("type") == "observation":
            steps.append({"kind": "output", "tool": last_tool, "body": r.get("content", "")})
        elif r.get("content"):
            steps.append({"kind": "text", "tool": "", "body": str(r["content"])})
    return steps


_CODEX_DONE = re.compile(r"\n (?:succeeded|exited -?\d+) in \d+ms:\n?")


def _codex(rows: list[dict]) -> list[dict]:
    steps = []
    for r in rows:
        body = r.get("content", "")
        if r.get("type") == "action" and r.get("raw_marker") == "exec":
            m = _CODEX_DONE.search(body)
            cmd = body[:m.start()] if m else body
            steps.append({"kind": "call", "tool": "exec", "body": cmd})
            if m:
                steps.append({"kind": "output", "tool": "exec", "body": body[m.start() + 1:]})
        elif r.get("type") == "action":
            steps.append({"kind": "call", "tool": r.get("raw_marker", ""), "body": body})
        elif body:
            steps.append({"kind": "text", "tool": "", "body": body})
    return steps


def _codex_events(rows: list[dict]) -> list[dict]:
    steps = []
    for r in rows:
        if r.get("type") != "item.completed":
            continue
        it = r.get("item") or {}
        t = it.get("type")
        if t == "reasoning" and it.get("text"):
            steps.append({"kind": "thinking", "tool": "", "body": it["text"]})
        elif t == "agent_message" and it.get("text"):
            steps.append({"kind": "text", "tool": "", "body": it["text"]})
        elif t == "command_execution":
            steps.append({"kind": "call", "tool": "exec", "body": it.get("command", "")})
            out = it.get("aggregated_output", "")
            if it.get("exit_code") not in (None, 0):
                out = f"exited {it.get('exit_code')} in\n{out}"
            steps.append({"kind": "output", "tool": "exec", "body": out})
        elif t == "file_change":
            steps.append({"kind": "call", "tool": "file_change",
                          "body": json.dumps(it.get("changes", []), ensure_ascii=False)})
    return steps


def load_steps(path: str | Path) -> list[dict]:
    rows = [json.loads(line) for line in open(path)]
    if not rows:
        return []
    if "message" in rows[0] or any("message" in r for r in rows[:5]):
        steps = _claude_code(rows)
    elif str(rows[0].get("type", "")).startswith("item."):
        steps = _codex_events(rows)
    elif "raw_marker" in rows[0]:
        steps = _codex(rows)
    else:
        steps = _gemini(rows)
    for i, s in enumerate(steps):
        s["i"] = i
    return steps


def is_exec_output(step: dict) -> bool:
    return step["kind"] == "output" and step["tool"] in ("Bash", "exec", "run_shell_command")
