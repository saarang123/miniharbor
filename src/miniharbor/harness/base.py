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

    def __init__(self, agent: Agent, tool_server: ToolServer, budgets: Budgets):
        self._agent = agent
        self._tools = tool_server
        self._budgets = budgets

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
                response = await self._agent.act(ctx)
            except Exception:
                # agent-level failure (a valid model result). A model-server infra
                # error is currently lumped here; distinguish later if needed.
                return self._finish(HaltReason.agent_failed, steps)

            action = response.action
            if action.tool == "submit":
                return self._finish(HaltReason.submitted, steps)

            # --- execute via the ToolServer ---
            # SandboxError (infra) is intentionally NOT caught here: it propagates to
            # the trial runner, which marks the trial infra_failed. Only the agent's
            # own errors count as agent_failed.
            observation = await self._tools.call(action.tool, action.args)

            # complete step: action + observation + the model I/O from AgentResponse.
            step = Step(
                index=len(steps), action=action, observation=observation,
                model_input=response.model_input, model_output=response.message,
                tokens_in=response.tokens_in, tokens_out=response.tokens_out,
                latency_ms=response.latency_ms,
            )
            steps.append(step)
            ctx.history.append(step)
            ctx.budgets_left.max_steps = self._budgets.max_steps - len(steps)

    def _finish(self, reason: HaltReason, steps: list[Step]) -> RunResult:
        # Logging is the trial runner's job (it owns the full Trajectory); the harness
        # just returns the loop result.
        return RunResult(halt_reason=reason, n_steps=len(steps), steps=steps)
