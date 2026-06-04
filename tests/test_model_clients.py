"""Unit tests for the ModelClient adapters' mapping logic (message split, request
build, response parse) -- the bug-prone parts -- without hitting any real API.
"""

from miniharbor.agent.model import AnthropicClient, SpindleClient
from miniharbor.models import Message


# --- Anthropic ---

def test_anthropic_splits_system_from_conversation():
    msgs = [
        Message(role="system", content="you are an agent"),
        Message(role="user", content="fix it"),
        Message(role="assistant", content="ok"),
        Message(role="user", content="Observation: ..."),
    ]
    system, conv = AnthropicClient._split_system(msgs)
    assert system == "you are an agent"
    assert [m["role"] for m in conv] == ["user", "assistant", "user"]   # no system in messages


def test_anthropic_parses_text_and_usage():
    data = {
        "content": [{"type": "text", "text": "hello "}, {"type": "text", "text": "world"}],
        "usage": {"input_tokens": 12, "output_tokens": 3},
    }
    r = AnthropicClient._parse(data)
    assert r.text == "hello world"
    assert r.tokens_in == 12 and r.tokens_out == 3


# --- Spindle ---

def test_spindle_builds_input_from_messages():
    msgs = [Message(role="user", content="hi")]
    inp = SpindleClient._build_input(msgs, {"temperature": 0.2})
    assert inp["messages"] == [{"role": "user", "content": "hi"}]
    assert inp["temperature"] == 0.2


def test_spindle_extracts_output_and_usage():
    client = SpindleClient("http://x", "text.generate", "cfg", output_key="completion")
    job = {"output": {"completion": "the answer", "usage": {"prompt_tokens": 5, "completion_tokens": 7}}}
    r = client._extract(job)
    assert r.text == "the answer"
    assert r.tokens_in == 5 and r.tokens_out == 7
