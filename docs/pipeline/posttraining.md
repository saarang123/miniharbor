# Post-training pipeline

Closes the loop. Trajectories produced by eval runs become training data; a small open model is trained on them; the improved model is re-evaluated on held-out tasks. This is the one place MiniHarbor extends past a Harbor clone — Harbor emits the trajectory format and hands off; here the ATIF → train → re-eval loop is wired end to end.

> Port: `TrainerBackend`. v1 adapter: LoRA SFT (offline). Swap-to: GRPO/RLVR (online, model server in the loop). DPO optional (see "Reward signal").

## The shape

```
Trajectory store (ATIF)
      │  filter + transform (pure function over stored records)
      ▼
training dataset (JSONL)
      │  TrainerBackend
      ▼
adapter (LoRA)
      │  load into ModelClient
      ▼
held-out eval (a job over tasks not used for training)
      │
      ▼
compare pass@1 vs the baseline model
```

The offline methods (SFT, DPO) are pure transforms over stored trajectories — no re-running rollouts. GRPO puts the model server back in the loop.

## Reward signal — verifiable rewards (RLVR)

The only signal here is the verifier's **pass/fail (or partial) reward per rollout** — there are no human preference labels. This is the **RLVR** (RL with Verifiable Rewards) setting, and it determines the natural method ladder:

- **SFT on passing rollouts** — uses the reward directly (filter to `reward.passed`). Simplest, offline, no instability. Do first.
- **GRPO** — the natural fit: it consumes exactly "N rollouts per task, each scored by a verifier," with group-relative advantage and no value/reward model. This is what the eval loop already produces (N trials per task → rewards), so the rollout collection *is* the benchmark fan-out.
- **DPO** — optional. It needs `(chosen ≻ rejected)` preference pairs, which must be *synthesized* from the binary outcomes (passed ≻ failed on the same task). Usable as an offline bridge, but a less natural fit than GRPO for a purely verifiable reward; the manufactured pairs are noisy.

So the recommended path for this reward signal is **SFT → GRPO**, with DPO as an optional offline step.

## Interface

```python
class TrainExample(BaseModel):
    prompt: list[Message]           # model_input
    completion: str                 # target action text / tool call
    weight: float = 1.0

class PreferencePair(BaseModel):
    prompt: list[Message]
    chosen: str
    rejected: str

class TrainerBackend(Protocol):
    async def train_sft(self, examples: list[TrainExample], base_model: str) -> str: ...   # → adapter ref
    async def train_dpo(self, pairs: list[PreferencePair], base_model: str) -> str: ...
    async def train_grpo(self, task_set: list[str], base_model: str, sampler: ModelClient) -> str: ...
```

Each returns an adapter reference that can be loaded into a `ModelClient` and pointed at a held-out eval job.

## Step 1 — baseline eval

Run the base open model as the agent over a task family, N attempts per task. Record the metrics that the trained runs are compared against:

```
pass@1 · pass@k · avg steps · timeout rate · invalid-command rate · avg runtime · tokens per trial
```

Keep a held-out split of tasks that the trainer never sees, so improvement is measured on unseen tasks, not memorized ones.

## Step 2 — SFT (behavior cloning), offline

```
select trajectories where reward.passed
for each trajectory, for each step:
    emit TrainExample(prompt=step.model_input, completion=step.model_output)
train_sft(examples, base_model)  →  LoRA adapter
```

"Imitate the runs that worked." Sources can be the base model's own successful rollouts, a stronger model's rollouts, or oracle traces (the reference solution replayed through the harness). The harness already logged `model_input`/`model_output`, so this is a flatten, not a re-run.

## Step 3 — DPO / preference, offline (optional)

Optional: with a purely verifiable reward, prefer SFT → GRPO (see "Reward signal").
DPO is a usable offline bridge if pursued. Build preference pairs from trajectories
on the same task:

```
passed ≻ failed
shorter-passed ≻ longer-passed
correct-final-state ≻ incorrect-final-state
```

```
train_dpo(pairs, base_model)  →  LoRA adapter
```

DPO is still offline — it consumes stored trajectories. It is the bridge between pure imitation and reward-driven optimization.

## Step 4 — GRPO / RLVR, online

```
for each task in task_set:
    sample K trajectories from the current policy (sampler = ModelClient with logprobs)
    reward each via the Verifier (pass/fail or partial)
    compute group-relative advantage within the K
    update the LoRA adapter
```

This is where the harness, the verifier, and the model server all run inside the training loop: the verifier's reward is the RL signal. It requires a `ModelClient` that returns token logprobs (the serving swap noted in [`../components/agent.md`](../components/agent.md)). Start after SFT/DPO are working, to avoid debugging training instability and infrastructure at the same time.

## Step 5 — held-out re-eval

Load the trained adapter into a `ModelClient`, run the same eval job over the held-out task split, and compare pass@1 against the baseline. The whole point is the delta on unseen tasks. Even a modest delta validates the end-to-end loop: rollouts → reward → trajectories → training → measurable improvement.

## Provenance and reproducibility

Every training set records the `harness_version` and `logger_version` of the trajectories it was built from. Mixing trajectories across incompatible scaffold versions changes the data distribution; the transform either filters to one version or records the mix explicitly. The trained adapter records which trajectories (by `trial_id`) and which transform produced it, so a result can be reproduced.

## What stays out of scope

Distributed/async trainer-rollout splits, weight distribution to many rollout workers, and large-model training are the production shape, not the v1 build. The `TrainerBackend` interface is where that complexity would later attach; v1 trains a small adapter in-process and proves the loop closes.
