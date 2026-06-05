"""The Run: one experiment = a set of tasks x N attempts for one agent.

This is the RUN-LEVEL layer that OWNS logging. run_trial is pure (returns a
Trajectory); the Run persists each trajectory via its logger, fans trials out with
a concurrency cap, aggregates pass@1 + status counts, and writes a run manifest.

To set up a new post-training experiment you point a Run at a task-set + give it a
logger + agent -- the run directory it produces (per-trial trajectory.json +
manifest.json) is the rollout dataset for SFT/GRPO.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import Callable

from .agent.base import Agent
from .environment.base import Environment
from .environment.docker import DockerEnvironment
from .logger import FileTrajectoryLogger, TrajectoryLogger
from .models import Budgets, RunReport, Task, TrialResult, TrialStatus
from .trial import run_trial
from .verifier import Verifier


async def run_job(
    tasks: list[Task],
    agent: Agent,
    *,
    run_dir: str,
    logger: TrajectoryLogger | None = None,
    env_factory: Callable[[Task], Environment] = DockerEnvironment,
    attempts: int = 1,
    concurrency: int = 4,
    verifier: Verifier | None = None,
    budgets: Budgets | None = None,
    run_id: str | None = None,
) -> RunReport:
    run_id = run_id or uuid.uuid4().hex[:12]
    logger = logger or FileTrajectoryLogger(run_dir)
    sem = asyncio.Semaphore(concurrency)
    specs = [(task, attempt) for task in tasks for attempt in range(attempts)]

    async def _one(task: Task, attempt: int) -> TrialResult:
        async with sem:                                   # concurrency cap
            trial_id = f"{task.task_id}-a{attempt}-{uuid.uuid4().hex[:6]}"
            traj = await run_trial(task, agent, env_factory(task),
                                   verifier=verifier, budgets=budgets, trial_id=trial_id)
            ref = logger.write(traj)                      # run owns persistence
            return TrialResult(
                trial_id=trial_id, task_id=task.task_id, status=traj.status,
                reward=traj.reward, halt_reason=traj.halt_reason, n_steps=traj.n_steps,
                duration_ms=traj.duration_ms, trajectory_ref=ref, error=traj.error,
            )

    trials = await asyncio.gather(*[_one(t, a) for (t, a) in specs])

    counts: dict[str, int] = {}
    for r in trials:
        counts[r.status.value] = counts.get(r.status.value, 0) + 1
    # pass rate over trials that produced a valid model result (exclude infra/verifier failures)
    valid = [r for r in trials if r.status not in (TrialStatus.infra_failed, TrialStatus.verifier_failed)]
    passed = sum(1 for r in trials if r.status == TrialStatus.passed)
    pass_at_1 = round(passed / len(valid), 4) if valid else 0.0

    report = RunReport(run_id=run_id, run_dir=run_dir, n_trials=len(trials),
                       status_counts=counts, pass_at_1=pass_at_1, trials=list(trials))
    os.makedirs(run_dir, exist_ok=True)
    with open(os.path.join(run_dir, "manifest.json"), "w") as fh:
        fh.write(report.model_dump_json(indent=2))
    return report
