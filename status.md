# Status

Living progress tracker. Update when a slice lands. For the design, read
[`README.md`](README.md) and [`docs/`](docs/); for the slice plan, read
[`docs/build-order.md`](docs/build-order.md).

> Rule for any contributor (human or agent): this repo is **hardware-blind** and
> impersonal. Never mention specific machines, hardware specs, or who is building
> it. Components are abstract roles (control-plane node, gpu/compute node, sandbox
> host, worker). Check before every commit.

## Phase

**Phase 1 — close one trial.** In progress.

## Done

- Design package: `docs/architecture.md`, `docs/data-model.md`, per-component docs
  in `docs/components/`, `docs/pipeline/posttraining.md`, `docs/build-order.md`,
  `docs/industry-comparison.md` (living).
- Example task bundles: `tasks/logfixbench/seed_001..003` (validated: bug present
  -> reward < 1; solution applies -> reward 1.0). Format in `tasks/README.md`.
- **Slice 1** — `Environment` ABC + models (`ExecResult`, `Resources`, `Budgets`,
  `VerifierSpec`, `Task`) + `SandboxError` + `Registry` ABC. `FakeEnvironment`.
- **Slice 2** — `DockerEnvironment` + `build_image`. Terminal protocol:
  `open_shell`/`exec(terminal_id=None|id)`/`close_shell`; one-shot vs persistent
  shell (sentinel done-detection); timeout always bounded; `read_file`/`write_file`
  via `docker cp`; `snapshot` via `docker commit`.
- Tests: models, FakeEnvironment, and the sentinel `_drain_until` protocol pass.
- **Slice 3** — DONE. Docker integration test (`tests/integration/`) passes on a
  real daemon: build `seed_001` → bug reproduces → fix via `write_file` →
  persistent-terminal session (cwd/env persist) → verifier injection → reward 1.0.
  Full suite: 12 passed. (The integration test caught a verifier bug: `run.sh`
  defaulted the `failed` count to 1, so a perfect run reported passed=False;
  fixed across all seeds.) Local daemon here is colima.

- **Slice 4** — DONE. `ToolServer` (per sandbox, env injected): 5 tools
  (`exec` with default persistent terminal, `open_shell`, `read_file`, `write_file`,
  `submit`), model-error-vs-infra-error split (`SandboxError` propagates), observation
  truncation, pinned `version`. Unit + Docker integration pass (21 total).

## Next (in build order)

- **Slice 5** — `Agent` base + a scripted stub agent.
- **Slice 6/7** — `Harness` loop + deterministic test with the stub agent.
- **Slice 8/9** — model-backed `Agent` + one real trial end-to-end.
- Then Phase 2 (Verifier/Logging/Orchestrator/Registry), Phase 3 (Firecracker),
  Phase 4 (SFT -> DPO -> GRPO).

## Open questions / deferred

- **Structured edit tool** — `read_file`/`write_file` are exposed, but a structured
  *edit* (str_replace-style) tool is deferred. Most frameworks have one; expect to
  add when eval shows the small model mangling whole-file rewrites / `sed` edits.
- **PTY / real TTY** — out of scope while the post-training target is non-interactive
  code tasks (SWE-bench/SWE-agent precedent). Becomes first-class only if the target
  capability becomes interactive terminal use (reshapes observation/action/trajectory).
  Swap-ready behind `open_shell`.
- **Persistence layer** (Store / ArtifactStore / queue) — deferred; null/in-memory
  for now. Add in Phase 2 when there are many trials to persist.

## Dev setup

```
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -q           # unit tests (docker integration auto-skips without docker)
.venv/bin/pytest tests/integration -q   # run on a host WITH docker for the full flow
```
