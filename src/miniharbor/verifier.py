"""The Verifier: compute a reward from the final sandbox state.

Not a class to subclass per task -- a black box with a file contract: inject the
task's tests/ into the sandbox, run the entrypoint, read the reward file. How the
reward is computed (pytest, a script, a binary) is opaque to the harness.
"""

from __future__ import annotations

import abc
import os

from .environment.base import Environment
from .models import Reward, Task


class Verifier(abc.ABC):
    @abc.abstractmethod
    async def verify(self, env: Environment, task: Task) -> Reward:
        """Grade the final state of `env` for `task` and return a Reward."""


class FileContractVerifier(Verifier):
    """Copy the task's tests/ into the sandbox at `inject_path` (a path the agent was
    never told about), run the entrypoint, and read the reward from `reward_path`.

    Uses the env's one-shot exec (terminal_id=None) -- isolated from the agent's
    terminals. v1 runs in the frozen container; the production swap runs it in a
    fresh grader booted from a snapshot, behind this same interface.
    """

    async def verify(self, env: Environment, task: Task) -> Reward:
        if not task.tests_ref:
            raise ValueError("task.tests_ref (host path to tests/) is required to verify")
        inject = task.verifier.inject_path
        for fname in sorted(os.listdir(task.tests_ref)):
            src = os.path.join(task.tests_ref, fname)
            if os.path.isfile(src):
                with open(src, "rb") as fh:
                    await env.write_file(f"{inject}/{fname}", fh.read())
        entry = os.path.basename(task.verifier.entrypoint)
        await env.exec(f"bash {inject}/{entry}", timeout_s=task.budgets.verifier_timeout_seconds)
        raw = await env.read_file(task.verifier.reward_path, max_bytes=100_000)
        return Reward.model_validate_json(raw)
