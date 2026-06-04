# Build order

The dependency-ordered slices to build MiniHarbor, each with a deliverable, what
it depends on, and an acceptance test. A slice is "done" only when its acceptance
test passes. Each slice maps to a component doc and is sized to be handed to one
agent.

The guiding rule: get **one real trial** to close end-to-end before adding
breadth (more environments, more tasks, the RL loop). Build the critical path
first; everything else hangs off a working loop.

## Phase 0 — done

- Design docs (`docs/`): the spine + one doc per component.
- Example task bundles (`tasks/logfixbench/seed_001..003`): the on-disk task
  contract, validated (bug present → reward < 1; solution applies → reward 1.0).

## Phase 1 — close one trial (critical path)

The whole phase exists to make this true: an agent runs against one task in a
real sandbox, gets graded by the hidden verifier, and produces a trajectory.

### Slice 1 — Environment interface + core models
- **Deliver:** the `Environment` Protocol and the shared Pydantic models
  (`ExecResult`, and the data-model types it touches). No implementation yet.
- **Depends on:** nothing.
- **Doc:** [`components/environment.md`](components/environment.md),
  [`data-model.md`](data-model.md).
- **Accept:** the interface imports; a `FakeEnvironment` (in-memory) satisfies it
  and can be used by later unit tests.

### Slice 2 — DockerEnvironment
- **Deliver:** the Docker adapter of `Environment`, async, shelling to the
  `docker` CLI via `asyncio.create_subprocess_exec`. Implements
  `start/exec/read_file/write_file/start_process/read_process_output/stop_process/snapshot/destroy`.
  `start` builds with context = bundle root, `-f environment/Dockerfile`; runs
  with `--network none` and the `task.toml` resource caps.
- **Depends on:** Slice 1.
- **Doc:** [`components/environment.md`](components/environment.md) (Docker adapter).
- **Accept:** Slice 3.

### Slice 3 — test DockerEnvironment (the vertical-slice proof)
- **Deliver:** an integration test that runs the real container flow on `seed_001`.
- **Depends on:** Slice 2, a working Docker daemon.
- **Accept:**
  ```
  1. build + run seed_001
  2. exec "python -m pytest tests_public.py -q"     → fails (bug present)
  3. write_file the fix (or exec patch -p1 < solution.patch)
  4. exec public tests                               → passes
  5. read tests/ into the container at /opt/verifier; exec "bash /opt/verifier/run.sh"
  6. read_file /logs/verifier/reward.json            → {"reward": 1.0, "passed": true}
  7. destroy
  ```
  This proves the Environment interface, verifier injection, and the reward
  contract on a real container.

### Slice 4 — ToolServer (typed/MCP)
- **Deliver:** the `ToolServer` bound to an `Environment`, exposing
  `bash`, `read_file`, `write_file`, `list_dir`, the process tools, and `submit`.
  Owns observation truncation (cap + `truncated`/`bytes_omitted`). Stateless
  `bash` + process tools — no PTY/tmux (see the optional slice in Phase 3).
- **Depends on:** Slice 1 (uses any `Environment`).
- **Doc:** [`components/tool-server.md`](components/tool-server.md).
- **Accept:** against a `DockerEnvironment` on `seed_001`, `call("bash", {...})`
  returns a structured `Observation`; a large output is truncated with the flags
  set; `call("write_file", ...)` then `bash` sees the change.

### Slice 5 — Agent base + stub agent
- **Deliver:** the `Agent` Protocol and `AgentConfig`/`Action`/`TrajectoryContext`
  models, plus a `ScriptedAgent` that replays a fixed list of actions. No model yet.
- **Depends on:** Slice 1.
- **Doc:** [`components/agent.md`](components/agent.md).
- **Accept:** `ScriptedAgent` yields its actions in order and a terminal `submit`;
  used as the deterministic driver for Slice 7.

### Slice 6 — Harness
- **Deliver:** the `Harness` loop: observe → `agent.act` → `tool_server.call` →
  log step → repeat until `submit` or a budget trips. Enforces `max_steps`,
  wall-clock, `max_tokens`. Carries a pinned `version`. Calls the logger hooks.
- **Depends on:** Slices 4, 5.
- **Doc:** [`components/harness.md`](components/harness.md).
- **Accept:** Slice 7.

### Slice 7 — test Harness with the stub agent
- **Deliver:** a deterministic test: `ScriptedAgent` (a known-good fix sequence) +
  `DockerEnvironment` + `ToolServer` on `seed_001`, run by the `Harness`.
- **Depends on:** Slices 3, 6.
- **Accept:** the loop runs the scripted actions, halts on `submit`, produces a
  step-by-step trajectory; a budget-trip variant halts with `timed_out`. No model
  involved — this isolates loop correctness from model behavior.

### Slice 8 — model-backed Agent
- **Deliver:** one model-backed `Agent` = `ModelClient` (OpenAI-compatible HTTP)
  + `PromptTemplate` + `Parser` (native tool-calling and/or ReAct text). The model
  is a config id, not a subclass — swapping models is config only. The model is
  served by an OpenAI-compatible endpoint (the serving setup is out of repo scope).
- **Depends on:** Slice 5.
- **Doc:** [`components/agent.md`](components/agent.md).
- **Accept:** given a `TrajectoryContext`, `act` calls the model and returns a
  valid `Action`; malformed model output is handled (retry once, else a recorded
  no-op / `agent_failed`).

### Slice 9 — integration: one real trial
- **Deliver:** model-backed `Agent` + `DockerEnvironment` + `ToolServer` + `Harness`
  on `seed_001`, graded by the verifier.
- **Depends on:** Slices 7, 8.
- **Accept:** a full trial runs against a live model, the verifier produces a
  reward, and a complete trajectory is emitted. The loop is closed.

## Phase 2 — make it a benchmark (many trials)

### Slice 10 — Verifier component
- **Deliver:** `FileContractVerifier` (run `entrypoint`, read `reward_path`),
  factored out of the Slice-3/9 inline flow. v1 runs in the frozen container.
- **Doc:** [`components/verifier.md`](components/verifier.md).
- **Accept:** returns a `Reward` for all three seeds; partial credit reflected in
  `breakdown`.

### Slice 11 — Logging / trajectory emit
- **Deliver:** `TrajectoryLogger` (JSONL sink), versioned, emitting the
  ATIF-compatible `Trajectory` with both halves of each step + pinned
  `harness_version`/`logger_version`.
- **Doc:** [`components/logging.md`](components/logging.md).
- **Accept:** a trial writes a replayable trajectory + indexed metrics.

### Slice 12 — Orchestrator (fan-out + collect)
- **Deliver:** expand a `Job` into `N × |task_set|` trials; `FifoScheduler` with a
  concurrency cap over a store-backed queue; run trials; collect pass@1/pass@k,
  timeout rate, infra-failure count.
- **Doc:** [`components/orchestrator.md`](components/orchestrator.md).
- **Accept:** `run --task logfixbench --attempts N --concurrency C` produces a
  per-job report; `infra_failed` trials are retried and excluded from pass-rate.

### Slice 13 — Registry + validation gates
- **Deliver:** the task catalog and the four-gate validation
  (builds / solvable / non-trivial / deterministic) over the bundle dir.
- **Doc:** [`components/registry.md`](components/registry.md).
- **Accept:** the three seeds register as `ready`; a deliberately-broken task is
  rejected with the failing gate named.

## Phase 3 — harden isolation (fancier environments)

These are swappable behind the `Environment` interface; none changes the loop.

### Slice 14 — FirecrackerEnvironment (the building-blocks lesson)
- **Deliver:** the microVM adapter, hand-rolled: flatten image layers → `rootfs.ext4`,
  supply a guest kernel, inject a vsock guest agent, boot via the Firecracker API
  (no network device), exec over vsock, native snapshot.
- **Doc:** [`components/environment.md`](components/environment.md) (Firecracker adapter).
- **Accept:** the Slice-3 acceptance test passes against `FirecrackerEnvironment`
  on `seed_001`, byte-for-byte the same test.

### Optional slices (add when a need appears, not before)
- **Kata / gVisor / hosted (E2B/Modal)** environment adapters.
- **PTY tool transport** — only if a task genuinely needs interactivity; a raw
  PTY-backed shell in the guest agent, never tmux.
- **Adapters** — convert external benchmarks (Terminal-Bench, SWE-bench) into the
  bundle format. Pure file transforms; independent of everything above.
- **Isolated grader** — run the verifier in a fresh sandbox booted from the final
  snapshot instead of the frozen container.

## Phase 4 — close the RL loop (the project goal)

### Slice 15 — SFT
- **Deliver:** transform passing trajectories → `(model_input, model_output)` pairs;
  LoRA SFT; load the adapter into a `ModelClient`.
- **Doc:** [`pipeline/posttraining.md`](pipeline/posttraining.md).
- **Accept:** held-out re-eval shows a pass@1 delta vs the baseline model.

### Slice 16+ — DPO, then GRPO
- **Deliver:** offline preference training (DPO), then online GRPO/RLVR with the
  model server in the loop and the verifier as the reward.
- **Accept:** each rung re-evaluated on the held-out split; the loop runs
  end-to-end (rollouts → reward → training → measurable improvement).

## Critical-path summary

```
1 Environment iface ─► 2 DockerEnv ─► 3 test DockerEnv ─┐
                                                        ├─► 9 one real trial
4 ToolServer ───────────────────────────────────────┐  │
5 Agent base + stub ─► 6 Harness ─► 7 test (stub) ───┴──┤
8 model-backed Agent ───────────────────────────────────┘
        │
        ▼
10 Verifier ─► 11 Logging ─► 12 Orchestrator ─► 13 Registry
        │
        ▼
14 Firecracker (and optional adapters)
        │
        ▼
15 SFT ─► 16 DPO ─► GRPO
```
