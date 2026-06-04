"""Slice 3 -- the vertical-slice proof: drive DockerEnvironment against the real
seed_001 bundle on a real Docker daemon.

Skipped automatically when `docker` is not available. Run on a Docker host with:
    pytest tests/integration -q
"""

import json
import os
import shutil

import pytest

from miniharbor.environment.docker import DockerEnvironment, build_image
from miniharbor.models import Task

pytestmark = pytest.mark.skipif(shutil.which("docker") is None, reason="docker not available")

BUNDLE = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "tasks", "logfixbench", "seed_001")
)


async def test_persistent_terminal_keeps_session_state():
    """A terminal preserves cwd and env vars across exec calls; a one-shot does not."""
    img = await build_image(BUNDLE)
    task = Task(task_id="seed_001", image_ref=img, instruction="(test)")
    async with DockerEnvironment(task) as env:
        tid = await env.open_shell()
        await env.exec("export FOO=bar", terminal_id=tid)
        r = await env.exec("echo $FOO", terminal_id=tid)
        assert "bar" in r.stdout                      # env var persisted

        await env.exec("cd /tmp", terminal_id=tid)
        r = await env.exec("pwd", terminal_id=tid)
        assert "/tmp" in r.stdout                      # cwd persisted

        # a one-shot (terminal_id=None) does NOT see the terminal's state
        r = await env.exec("echo ${FOO:-unset}")
        assert "unset" in r.stdout


async def test_full_trial_flow():
    """build -> bug present -> fix -> verifier reward = 1.0."""
    img = await build_image(BUNDLE)
    task = Task(task_id="seed_001", image_ref=img, instruction="(test)")
    async with DockerEnvironment(task) as env:
        # 1. public tests fail (the bug is present)
        r = await env.exec("python -m pytest tests_public.py -q", cwd="/workspace")
        assert r.exit_code != 0

        # 2. apply the reference solution (stand-in for the agent's fix)
        patch = open(os.path.join(BUNDLE, "solution", "solution.patch"), "rb").read()
        await env.write_file("/tmp/solution.patch", patch)
        r = await env.exec("patch -p1 < /tmp/solution.patch", cwd="/workspace")
        assert r.exit_code == 0

        # 3. public tests now pass
        r = await env.exec("python -m pytest tests_public.py -q", cwd="/workspace")
        assert r.exit_code == 0

        # 4. inject the hidden verifier and run it
        for fname in ("run.sh", "test_hidden.py"):
            content = open(os.path.join(BUNDLE, "tests", fname), "rb").read()
            await env.write_file(f"/opt/verifier/{fname}", content)
        await env.exec("bash /opt/verifier/run.sh", timeout_s=120)

        # 5. read the reward the verifier wrote
        reward = json.loads(await env.read_file("/logs/verifier/reward.json"))
        assert reward["passed"] is True
        assert reward["reward"] == 1.0
