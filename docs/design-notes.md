# Design notes & lessons

Cross-cutting observations that shaped the design — the non-obvious principles, and
the ones earned by running a real model and watching what broke. These aren't novel
research; they're the load-bearing engineering calls, written down so the *why*
survives. Component specifics live in [`components/`](components/); this is the
"things that are true across the system" layer.

---

## 1. Tool errors are observations; only a dead sandbox is infra

Anything that is a *consequence of the agent's action* — a command exiting nonzero,
a timeout, a bad `terminal_id`, a missing file path — is a **recoverable observation**
the agent sees and adapts to. Only the **whole sandbox dying** (daemon/container gone)
is an infra failure that aborts and retries the trial.

Why it matters: conflating the two corrupts the metric (a model mistake counted as
"infra" inflates infra-failure rate and silently drops a valid result) and crashes
trials that should have continued. So the error taxonomy is explicit:
`SandboxError` (infra → `infra_failed`, retryable, excluded from the model's score)
vs `TerminalError` / `FileNotFoundError` / bad-args (recoverable → an error observation).

**Earned the hard way:** running a strong model live surfaced two crashes that were
*false* infra failures — the model invoked `exec` with a `terminal_id` it never opened,
and `read_file` on a path that didn't exist. Both were the *model's* mistake; both were
being raised as `SandboxError` and aborting the trial. The fix was to reclassify them as
recoverable observations. The lesson: you don't find these by reading the code — you
find them by pointing a real policy at it. See [`components/toolserver.md`](components/toolserver.md).

## 2. Reward is per-trajectory (RLVR), not per-action

The verifier runs once, at the end, against the final state → one scalar per trial. No
per-step reward. This is the RL-with-verifiable-rewards setting, and it's the right one:
the verifier is the trustworthy signal, and per-step ("process") rewards need a separate
reward model and open a reward-hacking surface.

Credit assignment over the steps is the *algorithm's* job, not the reward's: GRPO applies
the trajectory-level (group-relative) advantage to every action in the trajectory. So a
sparse terminal reward is exactly GRPO's input — you don't need, and shouldn't add,
per-step rewards. Per-step reward is a classical-RL / reward-shaping / PRM concern; the
data model keeps a `Turn.reward` hook for it but leaves it unused. See
[`pipeline/posttraining.md`](pipeline/posttraining.md).

## 3. Single action per turn loses nothing on policy behavior

The agent emits one action per turn. This does **not** limit what the policy can express:
- **Interleaving subtasks** (`A1, B1, A2, B2, …`) is just a *sequence* of single actions —
  fully expressible, learned from the data/reward like any ordering. No special support.
- **Parallel tool calls** (firing two calls in one turn) is an *efficiency* optimization
  (fewer round-trips for independent ops), **not** a capability for solving harder tasks —
  a sequential agent solves anything a parallel one can, in more turns.

So single-action is the right default for clean `(observation, action)` training pairs, and
it caps no solving ability. A model only learns the action *format* it was trained on, so
firing multiple calls per turn is an opt-in extension (`AgentResponse.action` →
`tool_calls: list`), not something a single-action-trained model does spontaneously.

## 4. Cold-start: distill a strong teacher before RL

You cannot GRPO a model that solves ~0% of tasks zero-shot — every rollout scores 0, the
group-relative advantage is degenerate, and there is no gradient. RL amplifies behaviors a
model *already sometimes* exhibits; it can't create competence from nothing.

So bootstrap: run a strong teacher (a capable model **through our own harness**, so the
trajectories are already in the student's format), keep the passing rollouts, SFT the weak
student to a nonzero pass-rate, *then* GRPO. This is the cold-start-SFT-then-RL recipe.

## 5. Loop ownership: harness-owned (train) vs agent-owned (benchmark)

The harness owns the loop; the agent is a pure policy (`context → action`), blind to the
environment. That blindness is what makes a trajectory a clean training example and the
agent swappable. This is the **training** posture.

The alternative — the agent owns its own loop (an external CLI agent driving the sandbox) —
is the **benchmarking** posture (you can't restructure a black-box agent's loop, and can't
train it). Both are legitimate; they serve different goals. Usefully: our
`Harness + ToolServer + Agent` together ≈ *one* Harbor-style agent, so the two interoperate
in both directions. See [`industry-comparison.md`](industry-comparison.md) §3.

## 6. Logging is a run-level concern, not an agent-path one

`run_trial` is **pure**: it returns a `Trajectory` and persists nothing. The run-level
`Run` owns the logger, the output directory, fan-out, aggregation, and the manifest. This
keeps persistence off the agent/trial hot path, makes `run_trial` trivially testable (data
in → data out), and means a new experiment = a task-set + a `Run` with its own logger. The
harness *assembles* the trajectory (it's the only layer that sees both the agent's turn and
the observation); the toolserver stays a pure executor.

## 7. Scope: a terminal-agent runner, not a generic RL framework

The orchestration layer (`Run`: fan-out + aggregate) is domain-agnostic, but everything
below it commits to "an LLM agent driving a computer sandbox." The `Environment` port's
methods (`exec`/`read`/`write`) *are* that commitment — ports-and-adapters gives flexibility
*within* the paradigm (Docker → microVM → hosted), not *across* it (sandbox → physics sim).
Generic RL would mean a gym-style `step(action) → (obs, reward, done)` interface, which
already exists elsewhere; specializing to terminal agents (as Harbor does) is the deliberate
choice. Related orthogonality worth keeping straight: **session persistence ≠ a PTY** — a
persistent shell over a pipe keeps state but is not a terminal device (no `isatty()`, no
interactive TUIs); that's a separate, deferred capability.
