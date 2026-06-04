# Data model

The data contracts shared by every component: the on-disk task bundle, the Job/Trial lifecycle and status taxonomy, the trajectory (ATIF-compatible) and reward schemas. These are the interop boundaries — get them right and tasks, trajectories, and rewards are portable into the wider ecosystem.

## 1. Task bundle (on disk)

Copied from Harbor's layout. A task is a directory; the agent never sees anything but `instruction.md` and the running workspace.

```
<task>/
├── task.toml          # metadata, resources, budgets, verifier entrypoint
├── instruction.md     # the prompt shown to the agent
├── environment/       # image definition (Dockerfile or equivalent) + build context
│   └── Dockerfile
├── workspace/         # initial state mounted/baked at /workspace (the code to work on)
├── tests/             # hidden verifier: a runnable entrypoint that writes a reward file
│   └── run.sh
└── solution/          # optional reference solution (used for task validation)
    └── solution.patch
```

`task.toml`:

```toml
name = "logfixbench-001"
family = "logfixbench"
instruction_file = "instruction.md"

[budgets]
timeout_seconds = 600
max_steps = 40
max_tokens = 200000

[resources]
cpu = 2
memory_gb = 4
disk_gb = 8

[environment]
build = "environment/Dockerfile"     # image definition
workdir = "/workspace"

[verifier]
entrypoint = "tests/run.sh"          # standardized run command
reward_path = "/logs/verifier/reward.json"
```

The two verifier fields are the entire verifier contract: a command to run and a path to read a reward from. See [`components/verifier.md`](components/verifier.md).

## 2. Validation gates (a task is only usable if it passes)

A generated or authored task must pass these before it enters the registry as `ready`:

| Gate | Check | Rejects |
|---|---|---|
| Builds | the image definition builds | broken environment |
| Solvable | reference solution applied → verifier reward = pass | unsolvable / wrong tests |
| Non-trivial | empty/no-op agent → verifier reward = fail | task passes for free |
| Deterministic | verifier run twice on identical state → same reward | flaky verifier |

These map to the trial status taxonomy: a task that cannot pass these gates would produce meaningless rewards.

## 3. Job and Trial

A **Job** is a request to evaluate; it expands into independent **Trials**.

```python
class Budgets(BaseModel):
    timeout_seconds: int = 600
    max_steps: int = 40
    max_tokens: int = 200_000

class Job(BaseModel):
    job_id: str
    agent_cfg: AgentConfig          # which policy: model + prompt template + parser versions
    model: str                      # model identifier
    task_set: list[str]             # task names / dataset selector
    attempts: int                   # N runs per task
    budgets: Budgets
    harness_version: str            # pinned: scaffold version used for this job
    created_at: datetime

class TrialSpec(BaseModel):
    trial_id: str
    job_id: str
    task_name: str
    attempt: int                    # 0..attempts-1
    image_ref: str                  # content-addressed image / build ref
    agent_cfg: AgentConfig
    model: str
    budgets: Budgets
    harness_version: str
    verifier_ref: str               # entrypoint + reward_path from task.toml

class TrialResult(BaseModel):
    trial_id: str
    status: TrialStatus
    reward: Reward | None
    n_steps: int
    tokens_in: int
    tokens_out: int
    wall_clock_ms: int
    trajectory_ref: str             # artifact key
    snapshot_ref: str | None        # artifact key
    error: str | None               # set on infra/verifier failure
```

Expansion: a Job with `attempts=N` over `|task_set|=M` tasks produces `N × M` `TrialSpec`s. Each trial is independent and idempotent (keyed by `trial_id`) so it can be retried safely.

## 4. Trial status taxonomy

The distinction that keeps the metric honest: separate "the model did badly" from "our infra broke" from "the task is unhealthy."

```python
class TrialStatus(str, Enum):
    queued          = "queued"
    running         = "running"
    passed          = "passed"           # verifier reward = pass        → counts for the model
    failed_tests    = "failed_tests"     # verifier reward = fail        → counts for the model
    agent_failed    = "agent_failed"     # agent errored / gave up       → counts for the model
    timed_out       = "timed_out"        # budget trip                   → counts for the model
    infra_failed    = "infra_failed"     # sandbox/model/io error        → RETRYABLE, excluded from metric
    verifier_failed = "verifier_failed"  # verifier errored / nondeterministic → task is unhealthy
```

Rules:
- `passed`, `failed_tests`, `agent_failed`, `timed_out` are **valid model results** — they go into pass-rate.
- `infra_failed` is **retryable** and must not count against the model.
- `verifier_failed` flags the **task version** as unhealthy, not the model.

## 5. Reward

The verifier's output. Read from the file the task declares (`reward_path`).

```python
class Reward(BaseModel):
    reward: float                   # 0.0..1.0 (supports partial credit)
    passed: bool                    # pass/fail summary
    breakdown: dict[str, float] = {}  # per-test or per-criterion scores
    detail: str | None = None       # optional human-readable note
```

`reward.json` example written by `tests/run.sh`:

```json
{"reward": 1.0, "passed": true, "breakdown": {"test_no_dup": 1.0, "test_all_persisted": 1.0}}
```

The harness only parses this file. How the verifier computes it (pytest, a script, a binary) is opaque to the harness.

## 6. Trajectory (ATIF-compatible)

The full record of a trial: a header plus an ordered list of steps. Designed to (a) map onto Harbor's ATIF and (b) flatten directly into training examples for SFT/DPO/GRPO.

```python
class Action(BaseModel):
    tool: str                       # "bash" | "read_file" | "write_file" | "submit" | ...
    args: dict
    raw: str | None = None          # raw model text that produced this action (for parsing audit)

class Observation(BaseModel):
    tool: str
    result: dict                    # e.g. ExecResult fields for bash
    truncated: bool = False         # observation formatting truncated the result
    bytes_omitted: int = 0

class Message(BaseModel):
    role: str                       # "system" | "user" | "assistant" | "tool"
    content: str

class Step(BaseModel):
    index: int
    model_input: list[Message]      # exact messages sent to the model this step
    model_output: str               # raw model output (incl. reasoning if exposed)
    action: Action                  # parsed from model_output
    observation: Observation        # result of executing the action
    tokens_in: int
    tokens_out: int
    latency_ms: int

class Trajectory(BaseModel):
    trial_id: str
    task_name: str
    model: str
    agent_cfg: AgentConfig
    harness_version: str            # pinned scaffold version (comparability + train distribution)
    logger_version: str             # pinned logging schema version
    instruction: str
    steps: list[Step]
    final_status: TrialStatus
    reward: Reward | None
    started_at: datetime
    ended_at: datetime
```

Why both halves of each step are stored: training reconstructs "observation-history → next action." Without `model_input` and `observation` you cannot rebuild the policy's input; without `action` you have no target. Both `harness_version` and `logger_version` are pinned because either changing alters the data distribution and breaks comparability with past runs.

## 7. From trajectory to training data

The post-training pipeline ([`pipeline/posttraining.md`](pipeline/posttraining.md)) consumes `Trajectory` records:

| Method | Transform |
|---|---|
| SFT | keep trajectories with `reward.passed`; emit `(model_input, model_output)` pairs per step |
| DPO | pair trajectories on the same task: `passed ≻ failed`, or shorter-passed ≻ longer-passed |
| GRPO | sample K trajectories per task from the current policy; reward = verifier; group-relative advantage |

ATIF is the bridge format; SFT and DPO are offline transforms over stored trajectories, GRPO puts the model server back in the loop.

## 8. Identifiers and idempotency

- `trial_id` is deterministic from `(job_id, task_name, attempt)` so retries are idempotent (insert `ON CONFLICT DO NOTHING`).
- `image_ref` is content-addressed (a digest), never a mutable tag, so a trial always runs the exact image it was scheduled against.
- Artifact keys (`trajectory_ref`, `snapshot_ref`) are derived from `trial_id` so the store and artifact store stay consistent.
