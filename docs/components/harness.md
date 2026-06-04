# Harness

The fixed, versioned control loop. It owns the observe→act→observe cycle, enforces budgets, drives logging, and decides when a trial halts. It is fixed across agents within a run so two agents are compared under identical conditions, and it is versioned because changing the scaffold changes both eval comparability and the training-data distribution.

> Not a swap-port in the adapter sense — there is one harness. But it is **versioned and iterable**: the scaffold (prompt assembly, observation formatting, done-detection, budget policy) is tuned over time, and every version is pinned into the trajectory.

## Interface

```python
class Harness:
    version: str                       # pinned into every trajectory

    def __init__(self, agent: Agent, tool_server: ToolServer,
                 logger: TrajectoryLogger, budgets: Budgets): ...

    async def run(self, task: Task, env: Environment) -> TrialResult: ...
```

The harness is constructed with an `Agent`, a `ToolServer` (already bound to the `Environment`), a `TrajectoryLogger`, and `Budgets`. `run` drives one trial to completion and returns a `TrialResult`.

## The loop

```python
async def run(self, task, env):
    self.logger.on_trial_start(spec)
    context = TrajectoryContext(
        instruction=task.instruction,
        tool_schemas=self.tool_server.tool_schemas(),
        history=[],
        budgets_left=self.budgets,
    )
    status = TrialStatus.running
    while True:
        if self._budget_tripped(context):           # steps / wall-clock / tokens
            status = TrialStatus.timed_out
            break
        try:
            action = await self.agent.act(context)   # model decides
        except AgentError:
            status = TrialStatus.agent_failed
            break
        if action.tool == "submit":
            status = TrialStatus.running              # finalized after verify
            break
        observation = await self.tool_server.call(action.tool, action.args)
        step = Step(index=len(context.history), action=action, observation=observation, ...)
        self.logger.on_step(step)
        context.history.append(step)
        context.budgets_left = self._decrement(context.budgets_left, step)
    result = self._finalize(status, context)
    self.logger.on_trial_end(result)
    return result
```

`_finalize` records step count, token totals, and wall-clock. The verifier runs *after* `run` returns (in the worker), and its reward determines the terminal `passed` / `failed_tests` status when the agent submitted normally.

## Budgets

The harness is the single enforcement point:

| Budget | Tripped when | Status |
|---|---|---|
| `max_steps` | step count reaches the cap | `timed_out` |
| `timeout_seconds` | wall-clock exceeds the cap | `timed_out` |
| `max_tokens` | cumulative tokens exceed the cap | `timed_out` |

Budgets come from `task.toml`, overridable per job. Per-call `bash` timeouts are enforced by the ToolServer/Environment; the harness enforces the whole-trial budgets.

## What the harness version pins

The scaffold is everything that shapes the model's input and the loop's behavior, none of which is the agent's model weights:

- system prompt and how the instruction is presented
- how tool schemas are described to the model
- how prior `(action, observation)` history is formatted into messages
- observation truncation policy (cap, head/tail, markers)
- done-detection (what counts as `submit`; malformed-output handling)
- budget policy and how remaining budget is surfaced to the agent

Changing any of these is a new `harness_version`. Trajectories from different harness versions are not directly comparable and represent different training distributions. This is the multi-turn analogue of chat-template / tokenization drift: the contract must be pinned and logged.

## Iterating the harness

Keep the scaffold as data, not hardcoded strings: prompt assembly and observation formatting live in named, versioned templates. Iterating then means adding a version, running it, and comparing against the prior version on the same tasks — never silently editing the live scaffold. The version label is the unit of comparison and of training-data provenance.

## Relationship to the other components

- Calls `Agent.act` for the next action; calls `ToolServer.call` to execute it.
- Calls `TrajectoryLogger.on_step` each step and `on_trial_end` at halt.
- Does not call the `Verifier` (the worker does, after `run`), and does not construct the `Environment` (the worker does). The harness receives both already set up, which keeps it independent of the isolation backend.
