import pytest

from miniharbor.agent import ScriptedAgent
from miniharbor.environment import FakeEnvironment
from miniharbor.harness import Harness
from miniharbor.models import Action, Budgets, ExecResult, HaltReason
from miniharbor.toolserver import ToolServer


async def _harness(actions, responses=None, budgets=None):
    env = FakeEnvironment(responses or {})
    await env.start()
    return Harness(ScriptedAgent(actions), ToolServer(env), budgets or Budgets())


async def test_loop_runs_then_submits():
    h = await _harness(
        [Action(tool="exec", args={"command": "echo hi"}), Action(tool="submit")],
        responses={"echo hi": ExecResult(stdout="hi", stderr="", exit_code=0, duration_ms=1)},
    )
    r = await h.run("do it")
    assert r.halt_reason == HaltReason.submitted
    assert r.n_steps == 1                                   # exec counted; submit halts, not a step
    assert r.steps[0].observation.result["stdout"] == "hi"


async def test_budget_trips_to_timed_out():
    acts = [Action(tool="exec", args={"command": "x"})] * 5   # never submits
    h = await _harness(acts, budgets=Budgets(max_steps=2))
    r = await h.run("do it")
    assert r.halt_reason == HaltReason.timed_out
    assert r.n_steps == 2


async def test_agent_error_becomes_agent_failed():
    class _Boom(ScriptedAgent):
        async def act(self, context):
            raise RuntimeError("boom")

    env = FakeEnvironment()
    await env.start()
    h = Harness(_Boom([]), ToolServer(env), Budgets())
    r = await h.run("do it")
    assert r.halt_reason == HaltReason.agent_failed
