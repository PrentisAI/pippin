import asyncio

import pytest

from aurora.config import HumanConfig
from aurora.core.human_gate import HumanGate


def make_gate(**cfg):
    events = []

    async def publish(name, **data):
        events.append((name, data))

    return HumanGate(HumanConfig(**cfg), publish), events


async def test_autonomous_gates_continue_at_once(tmp_path):
    gate, events = make_gate(autonomy="autonomous")
    gate._log_path = tmp_path / "gates.jsonl"
    decision = await gate.request("milestone_audit", "title", "detail")
    assert decision.action == "continue" and decision.decided_by == "autonomous"
    assert [e[0] for e in events] == ["human_gate_opened", "human_gate_resolved"]
    assert gate._log_path.read_text().count("\n") == 1


async def test_supervised_gate_waits_for_the_human():
    gate, _ = make_gate(autonomy="supervised")
    task = asyncio.create_task(gate.request("supervisor", "t", "d"))
    await asyncio.sleep(0.01)
    [pending] = gate.pending
    assert not task.done()
    assert gate.resolve(pending.gate_id, "instruct", "use the larger basis set")
    decision = await task
    assert (decision.action, decision.message) == ("instruct", "use the larger basis set")
    assert gate.pending == []
    assert not gate.resolve(pending.gate_id, "continue")


async def test_switching_to_autonomous_releases_waiting_gates():
    gate, _ = make_gate(autonomy="supervised")
    task = asyncio.create_task(gate.request("supervisor", "t", "d"))
    await asyncio.sleep(0.01)
    gate.set_autonomy("autonomous")
    assert (await task).action == "continue"


async def test_timeout_takes_the_configured_action():
    gate, _ = make_gate(autonomy="supervised", gate_timeout_minutes=0.0005, on_timeout="stop")
    decision = await gate.request("termination", "t", "d")
    assert decision.action == "stop" and decision.decided_by == "timeout"


async def test_invalid_action_is_rejected():
    gate, _ = make_gate(autonomy="supervised")
    task = asyncio.create_task(gate.request("supervisor", "t", "d"))
    await asyncio.sleep(0.01)
    with pytest.raises(ValueError):
        gate.resolve(gate.pending[0].gate_id, "approve")
    gate.cancel_all()
    assert (await task).action == "stop"


async def test_propose_only_where_offered_and_with_text():
    gate, _ = make_gate(autonomy="supervised")
    plain = asyncio.create_task(gate.request("supervisor", "t", "d"))
    offered = asyncio.create_task(gate.request("screening", "t", "d",
                                               options=["continue", "instruct", "propose", "stop"]))
    await asyncio.sleep(0.01)
    first, second = gate.pending
    with pytest.raises(ValueError):
        gate.resolve(first.gate_id, "propose", "my idea")       # this gate does not offer it
    with pytest.raises(ValueError):
        gate.resolve(second.gate_id, "propose", "  ")           # a proposal needs its text
    assert gate.resolve(second.gate_id, "propose", "Test whether e-bikes flatten seasonality")
    assert (await offered).action == "propose"
    gate.resolve(first.gate_id, "stop")
    await plain


async def test_rerun_is_kept_only_for_propose():
    gate, _ = make_gate(autonomy="supervised")
    opts = ["continue", "instruct", "propose", "stop"]
    first = asyncio.create_task(gate.request("screening", "t", "d", options=opts))
    second = asyncio.create_task(gate.request("screening", "t", "d", options=opts))
    await asyncio.sleep(0.01)
    a, b = gate.pending
    gate.resolve(a.gate_id, "propose", "my idea", rerun=["screening", "bogus", "ideation"])
    gate.resolve(b.gate_id, "continue", "", rerun=["ideation"])
    assert (await first).rerun == ["ideation", "screening"]
    assert (await second).rerun == []
