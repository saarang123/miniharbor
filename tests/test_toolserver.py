import pytest

from miniharbor.environment import FakeEnvironment
from miniharbor.environment.base import SandboxError
from miniharbor.models import ExecResult
from miniharbor.toolserver import ToolServer


async def _ts(responses=None, max_obs_bytes=10_000):
    env = FakeEnvironment(responses or {})
    await env.start()
    return ToolServer(env, max_obs_bytes=max_obs_bytes)


async def test_schemas_present():
    ts = await _ts()
    assert {s.name for s in ts.tool_schemas()} == {
        "exec", "open_shell", "read_file", "write_file", "submit"
    }


async def test_exec_uses_default_terminal():
    ts = await _ts({"echo hi": ExecResult(stdout="hi", stderr="", exit_code=0, duration_ms=1)})
    obs = await ts.call("exec", {"command": "echo hi"})
    assert obs.tool == "exec"
    assert obs.result["stdout"] == "hi"
    assert obs.result["terminal_id"].startswith("term_")    # default terminal lazily opened


async def test_open_shell_returns_id():
    ts = await _ts()
    obs = await ts.call("open_shell", {})
    assert obs.result["terminal_id"].startswith("term_")


async def test_write_then_read():
    ts = await _ts()
    await ts.call("write_file", {"path": "/x", "content": "data"})
    obs = await ts.call("read_file", {"path": "/x"})
    assert obs.result["content"] == "data"


async def test_unknown_tool_is_recoverable_error():
    ts = await _ts()
    obs = await ts.call("nope", {})
    assert "error" in obs.result            # recoverable, not an exception


async def test_missing_arg_is_recoverable_error():
    ts = await _ts()
    obs = await ts.call("exec", {})         # missing "command"
    assert "error" in obs.result


async def test_truncation():
    big = "x" * 50_000
    ts = await _ts({"big": ExecResult(stdout=big, stderr="", exit_code=0, duration_ms=1)},
                   max_obs_bytes=1_000)
    obs = await ts.call("exec", {"command": "big"})
    assert obs.truncated is True
    assert obs.bytes_omitted > 0
    assert len(obs.result["stdout"]) < len(big)


async def test_sandbox_error_propagates():
    class _Raising(FakeEnvironment):
        async def exec(self, *a, **k):
            raise SandboxError("boom")

    env = _Raising()
    await env.start()
    ts = ToolServer(env)
    with pytest.raises(SandboxError):       # infra failure must NOT be swallowed
        await ts.call("exec", {"command": "x"})
