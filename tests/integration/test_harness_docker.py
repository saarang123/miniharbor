"""Slice 7 -- the stub-driven loop on a real task.

A scripted agent fixes seed_001 through the full stack (Harness -> ToolServer ->
DockerEnvironment), with no model involved, so loop correctness is isolated from
model behavior. Skips without a daemon.
"""

import os
import shutil
import subprocess

import pytest

from miniharbor.agent import ScriptedAgent
from miniharbor.environment.docker import DockerEnvironment, build_image
from miniharbor.harness import Harness
from miniharbor.models import Action, Budgets, HaltReason, Task
from miniharbor.toolserver import ToolServer


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=5).returncode == 0
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _docker_available(), reason="docker daemon not reachable")

BUNDLE = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "tasks", "logfixbench", "seed_001")
)


async def test_stub_agent_fixes_seed001_through_the_loop():
    img = await build_image(BUNDLE)
    instruction = open(os.path.join(BUNDLE, "instruction.md")).read()
    task = Task(task_id="seed_001", image_ref=img, instruction=instruction)

    buggy = open(os.path.join(BUNDLE, "workspace", "worker.py")).read()
    fixed = buggy.replace(
        "    # NOTE: events still buffered here after the loop are not handled.",
        "    if batch:\n        _flush(store, batch)",
    )

    async with DockerEnvironment(task) as env:
        tools = ToolServer(env)
        agent = ScriptedAgent([
            Action(tool="write_file", args={"path": "/workspace/worker.py", "content": fixed}),
            Action(tool="submit"),
        ])
        harness = Harness(agent, tools, Budgets(max_steps=10))

        result = await harness.run(task.instruction)

        assert result.halt_reason == HaltReason.submitted
        assert result.n_steps == 1                          # one write_file step

        # the loop's write_file actually landed -> public tests now pass
        obs = await tools.call("exec", {"command": "python -m pytest tests_public.py -q"})
        assert obs.result["exit_code"] == 0
