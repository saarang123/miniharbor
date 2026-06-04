"""ToolServer over a real DockerEnvironment on seed_001. Skips without a daemon."""

import os
import shutil
import subprocess

import pytest

from miniharbor.environment.docker import DockerEnvironment, build_image
from miniharbor.models import Task
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


async def test_toolserver_over_docker():
    img = await build_image(BUNDLE)
    task = Task(task_id="seed_001", image_ref=img, instruction="(test)")
    async with DockerEnvironment(task) as env:
        ts = ToolServer(env)

        # default terminal persists session state across exec calls
        await ts.call("exec", {"command": "export FOO=bar"})
        obs = await ts.call("exec", {"command": "echo $FOO"})
        assert "bar" in obs.result["stdout"]
        assert obs.result["exit_code"] == 0

        # write then read a file
        await ts.call("write_file", {"path": "/tmp/x", "content": "hello"})
        obs = await ts.call("read_file", {"path": "/tmp/x"})
        assert obs.result["content"] == "hello"
