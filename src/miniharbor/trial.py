"""The trial runner: run ONE trial end-to-end and return its Trajectory.

run_trial is PURE -- it returns the Trajectory (the full record) and does NOT
persist anything. Logging is a run-level concern owned by the Run (run.py), which
calls a TrajectoryLogger. This keeps logging out of the agent path and makes
run_trial trivially testable (data in -> data out).

It constructs the ToolServer and Harness from the (passed-in) env + agent because
those are per-trial (the toolserver binds to this env, the harness to that
toolserver). It owns the env lifecycle (start/destroy) and the model-result-vs-infra
distinction. Passed in: the task, the agent (a reusable policy), the env.
"""

from __future__ import annotations

import time
import uuid

from .agent.base import Agent
from .environment.base import Environment, SandboxError
from .harness import Harness
from .models import Budgets, HaltReason, Reward, Task, Trajectory, TrialStatus
from .toolserver import ToolServer
from .verifier import FileContractVerifier, Verifier


async def run_trial(
    task: Task,
    agent: Agent,
    env: Environment,
    *,
    verifier: Verifier | None = None,
    budgets: Budgets | None = None,
    trial_id: str | None = None,
) -> Trajectory:
    verifier = verifier or FileContractVerifier()
    budgets = budgets or task.budgets
    trial_id = trial_id or f"{task.task_id}-{uuid.uuid4().hex[:8]}"

    t0 = time.monotonic()
    reward: Reward | None = None
    error: str | None = None
    run_result = None

    try:
        async with env:                                   # owns start()/destroy()
            tools = ToolServer(env)
            harness = Harness(agent, tools, budgets)
            run_result = await harness.run(task.instruction)

            if run_result.halt_reason == HaltReason.agent_failed:
                status = TrialStatus.agent_failed
            elif run_result.halt_reason == HaltReason.timed_out:
                status = TrialStatus.timed_out
            else:                                         # submitted -> grade
                try:
                    reward = await verifier.verify(env, task)
                    status = TrialStatus.passed if reward.passed else TrialStatus.failed_tests
                except Exception as exc:                  # noqa: BLE001
                    status = TrialStatus.verifier_failed
                    error = f"verifier: {exc}"
    except SandboxError as exc:                           # whole sandbox died -> infra
        status = TrialStatus.infra_failed
        error = f"sandbox: {exc}"

    steps = run_result.steps if run_result else []
    return Trajectory(
        trial_id=trial_id,
        task_id=task.task_id,
        model=getattr(agent, "model_id", agent.name),
        agent=agent.name,
        agent_version=getattr(agent, "version", ""),
        harness_version=Harness.version,
        toolserver_version=ToolServer.version,
        instruction=task.instruction,
        steps=steps,
        halt_reason=run_result.halt_reason if run_result else None,
        status=status,
        reward=reward,
        n_steps=len(steps),
        duration_ms=int((time.monotonic() - t0) * 1000),
        error=error,
    )
