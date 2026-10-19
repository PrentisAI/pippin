import json
from types import SimpleNamespace

from aurora.human.frontdesk import FrontDeskAgent, campaign_tools


class FakeCompletions:
    """Returns scripted chat completions: first a tool call, then a text answer."""

    def __init__(self, script):
        self.script = list(script)
        self.requests = []

    async def create(self, **kwargs):
        self.requests.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=self.script.pop(0))])


def tool_call(name, args):
    call = SimpleNamespace(id="call_1", function=SimpleNamespace(name=name, arguments=json.dumps(args)))
    call.model_dump = lambda: {"id": "call_1", "type": "function",
                               "function": {"name": name, "arguments": json.dumps(args)}}
    return SimpleNamespace(content="", tool_calls=[call])


async def test_relays_an_instruction_and_answers(tmp_path):
    relayed, answered = [], []

    async def relay(message, job=""):
        relayed.append(message)
        return {"status": "delivered"}

    async def interrupt(reason, job=""):
        return {"status": "interrupted"}

    async def answer_gate(gate_id, action, message, rerun=None):
        answered.append((gate_id, action))
        return {"status": "recorded"}

    (tmp_path / "notes.txt").write_text("hello")
    agent = FrontDeskAgent(model="m", api_key="k", base_url="u", workspace_root=tmp_path,
                           tools=campaign_tools(relay=relay, interrupt=interrupt, answer_gate=answer_gate),
                           status_fn=lambda: "Phase: research")
    fake = FakeCompletions([tool_call("relay_to_agent", {"message": "use 5-fold CV"}),
                            SimpleNamespace(content="Done, the agent got it.", tool_calls=None)])
    agent._client = SimpleNamespace(chat=SimpleNamespace(completions=fake))
    reply = await agent.handle_user_message("tell it to use cross-validation")
    assert relayed == ["use 5-fold CV"]
    assert reply["text"] == "Done, the agent got it."
    assert "Phase: research" in fake.requests[0]["messages"][-1]["content"]
    names = {t["function"]["name"] for t in fake.requests[0]["tools"]}
    assert {"relay_to_agent", "interrupt_agent", "answer_gate", "read_file", "list_files"} <= names


async def test_file_tools_stay_inside_the_workspace(tmp_path):
    agent = FrontDeskAgent(model="m", api_key="k", base_url="u", workspace_root=tmp_path)
    (tmp_path / "a.txt").write_text("content")
    assert (await agent._call_tool("read_file", json.dumps({"path": "a.txt"})))["content"] == "content"
    result = await agent._call_tool("read_file", json.dumps({"path": "../../etc/passwd"}))
    assert "error" in result
