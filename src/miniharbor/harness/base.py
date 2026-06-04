"""The Harness: the fixed, versioned control loop.

Owns the observe -> act -> execute cycle, the history-of-record, budget enforcement,
termination, and (later) logging. The agent decides the action; the ToolServer
executes it; the harness mediates and is blind to neither but owns neither.

Versioned: the scaffold (how context is assembled, budgets enforced, termination
decided) is pinned per run, because changing it changes eval comparability and the
training-data distribution.
"""

from __future__ import annotations

import time

from ..agent.base import Agent
from ..models import Budgets, HaltReason, RunResult, Step, TrajectoryContext
from ..toolserver.base import ToolServer

HARNESS_VERSION = "v1"


class Harness:
    version = HARNESS_VERSION

    def __init__(self, agent: Agent, tool_server: ToolServer, budgets: Budgets, *, logger=None):
        self._agent = agent
        self._tools = tool_server
        self._budgets = budgets
        self._logger = logger          # TrajectoryLogger (Slice 11); optional for now

    async def run(self, instruction: str) -> RunResult:
        ctx = TrajectoryContext(
            instruction=instruction,
            tool_schemas=self._tools.tool_schemas(),
            history=[],
            budgets_left=self._budgets.model_copy(deep=True),
        )
        steps: list[Step] = []
        start = time.monotonic()

        while True:
            # --- budget checks (harness is the single enforcement point) ---
            if len(steps) >= self._budgets.max_steps:
                return self._finish(HaltReason.timed_out, steps)
            if time.monotonic() - start > self._budgets.timeout_seconds:
                return self._finish(HaltReason.timed_out, steps)

            # --- ask the policy for the next action ---
            try:
                action = await self._agent.act(ctx)
            except Exception:
                # agent-level failure (a valid model result). NOTE: a model-server
                # infra error will be distinguished from this at Slice 8.
                return self._finish(HaltReason.agent_failed, steps)

            if action.tool == "submit":
                return self._finish(HaltReason.submitted, steps)

            # --- execute via the ToolServer ---
            # SandboxError (infra) is intentionally NOT caught here: it propagates to
            # the worker, which marks the trial infra_failed. Only the agent's own
            # errors count as agent_failed.
            observation = await self._tools.call(action.tool, action.args)

            # model_output captured from the action's raw text (model agents set it;
            # scripted agents leave it empty). Full model_input/token capture lands
            # with the logger in Slice 11.
            step = Step(index=len(steps), action=action, observation=observation,
                        model_output=action.raw or "")
            steps.append(step)
            ctx.history.append(step)
            ctx.budgets_left.max_steps = self._budgets.max_steps - len(steps)
            if self._logger is not None:
                self._logger.on_step(step)

    def _finish(self, reason: HaltReason, steps: list[Step]) -> RunResult:
        result = RunResult(halt_reason=reason, n_steps=len(steps), steps=steps)
        if self._logger is not None:
            self._logger.on_trial_end(result)
        return result
