import pytest

from miniharbor.environment import FakeEnvironment
from miniharbor.environment.base import Environment
from miniharbor.models import ExecResult


async def test_context_manager_and_scripted_exec():
    resp = {"echo hi": ExecResult(stdout="hi", stderr="", exit_code=0, duration_ms=1)}
    async with FakeEnvironment(resp) as e:
        r = await e.exec("echo hi")
        assert r.stdout == "hi"


async def test_terminal_lifecycle():
    e = FakeEnvironment()
    await e.start()
    tid = await e.open_shell()
    assert tid.startswith("term_")
    await e.close_shell(tid)
    await e.destroy()


async def test_filesystem_roundtrip():
    e = FakeEnvironment()
    await e.start()
    await e.write_file("/x", b"data")
    assert await e.read_file("/x") == b"data"


def test_abc_enforces_method_set():
    with pytest.raises(TypeError):
        class Broken(Environment):
            pass

        Broken()
