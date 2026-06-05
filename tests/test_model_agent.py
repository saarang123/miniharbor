import pytest

from miniharbor.agent.model import (
    DefaultPromptTemplate,
    FakeModelClient,
    JSONActionParser,
    ModelAgent,
)
from miniharbor.models import ToolSchema, TrajectoryContext


# --- parser ---

def test_parser_extracts_fenced_json():
    a = JSONActionParser().parse('thinking...\n```json\n{"tool":"exec","args":{"command":"ls"}}\n```')
    assert a.tool == "exec"
    assert a.args["command"] == "ls"


def test_parser_falls_back_to_last_braces():
    a = JSONActionParser().parse('blah {"tool":"submit","args":{}} trailing')
    assert a.tool == "submit"


def test_parser_returns_none_on_garbage():
    assert JSONActionParser().parse("no json here at all") is None


# --- prompt template ---

def test_prompt_renders_system_then_instruction():
    ctx = TrajectoryContext(
        instruction="fix the bug",
        tool_schemas=[ToolSchema(name="exec", description="run a command",
                                 parameters={"properties": {"command": {}}})],
    )
    msgs = DefaultPromptTemplate().render(ctx)
    assert msgs[0].role == "system" and "exec" in msgs[0].content
    assert msgs[1].role == "user" and msgs[1].content == "fix the bug"


# --- model agent ---

async def test_act_returns_parsed_action():
    client = FakeModelClient(['```json\n{"tool":"exec","args":{"command":"echo hi"}}\n```'])
    r = await ModelAgent(client).act(TrajectoryContext(instruction="do it"))
    assert r.action.tool == "exec" and r.action.args["command"] == "echo hi"
    assert r.message and r.model_input            # model I/O captured for the trajectory


async def test_act_recovers_on_corrective_retry():
    client = FakeModelClient(["garbage", '```json\n{"tool":"submit","args":{}}\n```'])
    r = await ModelAgent(client).act(TrajectoryContext(instruction="do it"))
    assert r.action.tool == "submit"


async def test_act_raises_after_failed_retry():
    client = FakeModelClient(["garbage", "still garbage"])
    with pytest.raises(ValueError):
        await ModelAgent(client).act(TrajectoryContext(instruction="do it"))
