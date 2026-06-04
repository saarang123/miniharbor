# Logging

The explicit trajectory logger wired into the harness. It records every step as it happens and emits a versioned, ATIF-compatible `Trajectory` whose output flows directly into post-training. It is a class-based, versioned module so the captured schema can be tuned without silently changing what past runs meant.

> Port. v1 adapter: JSONL files on local disk. Swap-to: object store; trajectories streamed to the trainer.

## Why logging is its own module

Two reasons it is not just `print` calls inside the harness:

1. **It is the product.** The trajectory plus reward is simultaneously the eval record and the RL training example. Its completeness and format determine whether the post-training loop is even possible.
2. **It is versioned.** What fields are captured, and how observations are serialized, is a schema that must be pinned (`logger_version`) so a trajectory's meaning is stable. Changing the logged schema is a version bump, not an edit.

## Interface

```python
class TrajectoryLogger(Protocol):
    version: str
    def on_trial_start(self, trial: TrialSpec) -> None: ...
    def on_step(self, step: Step) -> None: ...
    def on_trial_end(self, result: TrialResult) -> None: ...
    def emit(self) -> Trajectory: ...
```

The harness calls `on_trial_start` once, `on_step` per step, `on_trial_end` at halt, then `emit()` to produce the `Trajectory` that is written to the artifact store and indexed in the metadata store.

## What every step captures

Both halves of every step, because training reconstructs "observation-history → next action":

```python
class Step(BaseModel):
    index: int
    model_input: list[Message]    # exact messages sent to the model this step
    model_output: str             # raw model output (incl. reasoning if exposed)
    action: Action                # parsed tool call
    observation: Observation      # structured result of executing it
    tokens_in: int
    tokens_out: int
    latency_ms: int
```

And the trajectory header pins provenance:

```python
class Trajectory(BaseModel):
    trial_id: str
    task_name: str
    model: str
    agent_cfg: AgentConfig
    harness_version: str          # scaffold version
    logger_version: str           # this logger's schema version
    instruction: str
    steps: list[Step]
    final_status: TrialStatus
    reward: Reward | None
    started_at: datetime
    ended_at: datetime
```

Log generously: un-captured observations cannot be recovered later, and a missing field can invalidate a whole batch of trajectories for training.

## Versioning the schema

`logger_version` is bumped whenever the captured fields or their serialization change. Trajectories carry their version so a downstream transform can branch on it (or refuse to mix incompatible versions in one training set). This mirrors `harness_version`: together they describe the exact conditions a trajectory was produced under.

## Sinks (the swap)

- **v1 — JSONL.** One file per trial under a run directory; the metadata store holds the `trajectory_ref` (path) plus indexed metrics. Trivial to inspect with standard tools.
- **Swap-to — object store.** Same `Trajectory` serialized to an S3-compatible store keyed by `trial_id`, with a retention TTL. The metadata store holds the key.
- **Swap-to — streamed.** For online RL, `emit()` (or `on_step`) pushes to a queue/stream the trainer consumes, so rollouts feed training without a batch round-trip.

The `Trajectory` schema is identical across sinks; only where bytes land changes.

## ATIF compatibility and the post-training hand-off

The `Trajectory` schema is designed to map onto Harbor's ATIF (Agent Trajectory Interchange Format) so trajectories are portable into the wider RL/optimization ecosystem. The post-training pipeline ([`../pipeline/posttraining.md`](../pipeline/posttraining.md)) reads `Trajectory` records directly:

- SFT: filter `reward.passed`; flatten each step to a `(model_input, model_output)` pair.
- DPO: pair trajectories on the same task into `(chosen, rejected)`.
- GRPO: group K trajectories per task; reward from the stored `Reward`.

Because the logger already records `model_input`, `model_output`, and per-step tokens, these transforms are pure functions over stored trajectories — no re-running required for the offline methods.

## Metrics derived from logs

Aggregate metrics for the dashboard/report are computed from trajectories and trial results, not logged separately: pass@1 and pass@k, average steps, timeout rate, invalid-command rate (parser failures), average runtime, tokens per trial. Keeping them derived (not duplicated) means a fix to the computation re-derives cleanly from the source records.
