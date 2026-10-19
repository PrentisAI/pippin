"""Pausing the research agent keeps its session: it waits, then goes on with the next message."""

import asyncio
import json
import os
import stat
import sys
import types

import pytest

from aurora.config import AuroraConfig
from aurora.core.engine import ResearchEngine
from aurora.core.terminal_iteration import TerminalIteration
from aurora.llm.agent_loops import ClaudeCodeLoop

# A stand-in for `claude -p --input-format stream-json`: the first message "works" for a while
# (an interrupt control request cuts it short); every later message gets a reply at once.
FAKE_CLAUDE = r'''
import json, select, sys, time
def out(o):
    sys.stdout.write(json.dumps(o) + "\n"); sys.stdout.flush()
out({"type": "system", "subtype": "init", "session_id": "s1"})
first = True
while True:
    line = sys.stdin.readline()
    if not line:
        break
    msg = json.loads(line)
    if msg.get("type") == "control_request":
        continue
    text = msg["message"]["content"][0]["text"]
    if first:
        first = False
        end = time.time() + 2.0
        cut = False
        while time.time() < end:
            r, _, _ = select.select([sys.stdin], [], [], 0.05)
            if r:
                nxt = json.loads(sys.stdin.readline())
                if nxt.get("type") == "control_request":
                    out({"type": "control_response", "response": {"subtype": "success"}})
                    out({"type": "result", "subtype": "error_during_execution", "is_error": True,
                         "result": "", "total_cost_usd": 0.01})
                    cut = True
                    break
        if not cut:
            out({"type": "assistant", "message": {"content": [{"type": "text", "text": "DONE"}]}})
            out({"type": "result", "subtype": "success", "is_error": False, "result": "DONE",
                 "total_cost_usd": 0.02})
        continue
    out({"type": "assistant", "message": {"content": [{"type": "text", "text": "REPLY:" + text}]}})
    out({"type": "result", "subtype": "success", "is_error": False, "result": "REPLY:" + text,
         "total_cost_usd": 0.03})
'''


@pytest.fixture()
def fake_claude(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    exe = bin_dir / "claude"
    exe.write_text(f"#!{sys.executable}\n{FAKE_CLAUDE}")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return tmp_path


def make_loop(cwd):
    return ClaudeCodeLoop(model="m", api_key="k", base_url="http://localhost", cwd=str(cwd), max_turns=20)


async def test_paused_session_waits_and_goes_on(fake_claude):
    loop = make_loop(fake_claude)
    task = asyncio.create_task(loop.run("sys", "work"))
    await asyncio.sleep(0.5)
    loop.interrupt()
    await asyncio.sleep(1.0)
    assert loop.paused and not task.done()            # the session waits for the next message
    await loop.inject_message("go on")
    result = await asyncio.wait_for(task, 10)
    await loop.close()
    assert result["final_text"] == "REPLY:go on" and result["error"] is None


async def test_uninterrupted_session_ends_with_its_reply(fake_claude):
    loop = make_loop(fake_claude)
    result = await asyncio.wait_for(loop.run("sys", "work"), 10)
    await loop.close()
    assert result["final_text"] == "DONE" and result["error"] is None


async def test_interrupt_then_message_at_once(fake_claude):
    """The time limit: stop the agent and ask for a summary right away."""
    loop = make_loop(fake_claude)
    task = asyncio.create_task(loop.run("sys", "work"))
    await asyncio.sleep(0.5)
    loop.interrupt()
    await loop.inject_message("summary please")
    result = await asyncio.wait_for(task, 10)
    await loop.close()
    assert result["final_text"] == "REPLY:summary please" and result["error"] is None


async def test_paused_time_does_not_count_against_the_job():
    it = object.__new__(TerminalIteration)
    it._paused_since, it._paused_total = None, 0.0
    task = asyncio.create_task(asyncio.sleep(0.8))
    it._paused_since = __import__("time").time()       # paused from the start
    asyncio.get_running_loop().call_later(0.6, it._resume_clock)
    assert await it._wait_working(task, 0.3)           # 0.3 s of work allowed, but 0.6 s were paused


class Runner:
    def __init__(self):
        self.paused, self.resumed = False, []

    def pause_for_decision(self):
        self.paused = True

    async def resume_after_decision(self, text):
        self.paused = False
        self.resumed.append(text)


@pytest.mark.parametrize("action, decided_by, expect", [
    ("continue", "human", "Continue where you stopped"),
    ("instruct", "human", "use the smaller sample"),
    ("continue", "auditor", "withdrew the question"),
])
async def test_live_auditor_gate_pauses_until_the_decision(tmp_path, action, decided_by, expect):
    engine = ResearchEngine(AuroraConfig(objective="x", mock_mode=True, campaign_id="p1",
                                         workspace={"root": str(tmp_path)}, human={"autonomy": "supervised"}))
    from aurora.core.terminal_iteration import RunningJobs
    runner = Runner()
    group = RunningJobs()
    group.add("job-a", runner)
    engine.current_iteration_runner = group
    gate = asyncio.create_task(engine._supervisor_gate(types.SimpleNamespace(reason="loops", suggestion="s",
                                                                             job="job-a")))
    for _ in range(500):
        if engine.human_gate.pending or gate.done():
            break
        await asyncio.sleep(0.01)
    assert not gate.done(), gate.exception() if gate.done() else None
    assert runner.paused
    [g] = engine.human_gate.pending
    if decided_by == "auditor":
        engine.human_gate.close_source("supervisor", "no decision needed", decided_by="auditor")
    else:
        engine.human_gate.resolve(g.gate_id, action, "use the smaller sample" if action == "instruct" else "")
    await gate
    assert not runner.paused and expect in runner.resumed[0]


async def test_messages_wait_with_a_paused_agent():
    it = object.__new__(TerminalIteration)
    it._paused_since, it._paused_total = None, 0.0
    it._awaiting_decision, it._held_messages, it._pending_messages = False, [], []
    sent = []

    class Loop:
        paused = False

        def interrupt(self):
            pass

        async def inject_message(self, m):
            sent.append(m)

    it._current_loop = Loop()
    it.pause_for_decision()
    await it.inject_message("[Live auditor — advice] check the units")
    assert sent == []                                   # does not wake the agent
    await it.resume_after_decision("[Decision] continue")
    assert sent == ["[Decision] continue\n\n[Live auditor — advice] check the units"]


def test_verdict_withdraw_is_parsed():
    from aurora.core.supervisor import parse_verdicts
    from aurora.models.audit import AuditOutcome
    [v] = parse_verdicts('ok\nVERDICT {"outcome": "withdraw", "reason": "a single-file copy is allowed"}')
    assert v.outcome == AuditOutcome.WITHDRAW
    assert json.dumps(v.to_dict())
