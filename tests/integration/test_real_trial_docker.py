"""Slice 9 -- one real trial with a live model.

Runs only when BOTH a docker daemon AND a model endpoint are configured:
    MINIHARBOR_MODEL_BASE_URL=http://localhost:11434/v1   (e.g. an Ollama server)
    MINIHARBOR_MODEL=qwen3:4b
This is a smoke test: it asserts the loop runs and terminates with a live policy,
NOT that a small model passes the task (pass-rate is what post-training is for).
"""

import os
import shutil
import subprocess

import pytest

from miniharbor.agent import ModelAgent, OpenAIChatClient
from miniharbor.environment.docker import DockerEnvironment, build_image
from miniharbor.harness import Harness
from miniharbor.models import Budgets, HaltReason, Task
from miniharbor.toolserver import ToolServer

BASE_URL = os.environ.get("MINIHARBOR_MODEL_BASE_URL")
MODEL = os.environ.get("MINIHARBOR_MODEL")


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=5).returncode == 0
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not (_docker_available() and BASE_URL and MODEL),
    reason="needs a docker daemon AND MINIHARBOR_MODEL_BASE_URL + MINIHARBOR_MODEL",
)

BUNDLE = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "tasks", "logfixbench", "seed_001")
)


async def test_one_real_trial_runs_and_terminates():
    img = await build_image(BUNDLE)
    instruction = open(os.path.join(BUNDLE, "instruction.md")).read()
    task = Task(task_id="seed_001", image_ref=img, instruction=instruction)

    async with DockerEnvironment(task) as env:
        tools = ToolServer(env)
        agent = ModelAgent(OpenAIChatClient(BASE_URL, MODEL))
        harness = Harness(agent, tools, Budgets(max_steps=20))

        result = await harness.run(task.instruction)

        # the loop ran with a live model and terminated cleanly (any halt is valid)
        assert result.halt_reason in (
            HaltReason.submitted, HaltReason.timed_out, HaltReason.agent_failed
        )
        assert result.n_steps >= 0
