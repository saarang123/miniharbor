# Verifier

Computes the reward from the final sandbox state. It is not a class to subclass — it is a black box with two components: a **standardized run command** and a **reward written to a file**. The harness only knows how to run the command and read the file; how the reward is computed (pytest, a script, a binary) is opaque.

> Port. v1 adapter: run the task's entrypoint in the frozen sandbox. Swap-to: run it in an isolated grader sandbox.

## The contract (the entire design)

From `task.toml`:

```toml
[verifier]
entrypoint = "tests/run.sh"          # the standardized run command
reward_path = "/logs/verifier/reward.json"
```

The harness/worker does exactly two things:

1. Run the entrypoint inside the environment.
2. Read and parse the reward file at `reward_path`.

```python
class Verifier(Protocol):
    async def verify(self, env: Environment, task: Task) -> Reward: ...

class FileContractVerifier:
    async def verify(self, env, task):
        await env.exec(f"bash {task.verifier.entrypoint}", timeout_s=task.budgets.verifier_timeout)
        raw = await env.read_file(task.verifier.reward_path)
        return Reward.model_validate_json(raw)
```

`Reward`:

```python
class Reward(BaseModel):
    reward: float                     # 0.0..1.0
    passed: bool
    breakdown: dict[str, float] = {}
    detail: str | None = None
```

That is the whole interface. The grading logic lives entirely on the task side.

## What the entrypoint does

Anything executable that writes the reward file. Typical pattern:

```bash
# tests/run.sh
mkdir -p /logs/verifier
pytest -q tests/hidden_test.py --json-report --json-report-file=/tmp/r.json || true
python - <<'PY'
import json
r = json.load(open("/tmp/r.json"))["summary"]
total = r.get("total", 0) or 1
passed_n = r.get("passed", 0)
json.dump(
    {"reward": passed_n / total,
     "passed": r.get("failed", 1) == 0 and r.get("error", 0) == 0,
     "breakdown": {"passed": passed_n, "total": total}},
    open("/logs/verifier/reward.json", "w"),
)
PY
```

Use `reward.json` (structured) over a bare float file so partial credit and a per-test breakdown come for free and feed reward shaping later.

## Hidden from the agent

The `tests/` directory and the reference `solution/` are never mounted into the agent's view and never appear in the instruction. The agent works against `workspace/`; the verifier is copied in (or already present at a path the agent is not told about) and run only after the agent halts. If the agent can read the tests, it will hardcode their expected output, and the reward becomes meaningless.

## Where the verifier runs (the swap)

- **v1 — in the frozen sandbox.** After the agent halts, stop its processes and run the entrypoint in the same environment. Simple, but the agent's run could have altered the environment (left a process, changed `PATH`, monkeypatched a module).
- **Swap-to — isolated grader.** Snapshot the agent's final filesystem, boot a fresh clean environment from it, copy the hidden tests in, and run there. The agent cannot have tampered with the grader's runtime. The `verify` interface is identical; only where it executes changes.

## Reward correctness is the product

A wrong or gameable verifier poisons every number downstream and any training signal. The verifier is validated as part of task admission (see [`../data-model.md`](../data-model.md) §2):

- reference solution → reward = pass (the task is solvable and the tests are right)
- empty/no-op agent → reward = fail (the task is not trivially satisfied)
- run twice on identical state → identical reward (the verifier is deterministic; otherwise the task is flagged `verifier_failed`)

These gates are what separate a real benchmark from a leaderboard of noise.

## Reward hacking

Even a hidden, correct verifier can be gamed if the reward is shaped carelessly: an agent may satisfy the letter of a test while violating its intent, or exploit a weak invariant. Mitigations: test invariants rather than exact outputs where possible; keep multiple independent checks in `breakdown`; and watch for trajectories that pass with implausibly few or strange steps. Reward hacking can emerge from the optimization itself once the visible signal is saturated, so the verifier's robustness matters more as the post-training loop tightens.
