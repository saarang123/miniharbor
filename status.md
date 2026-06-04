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
- Tests: models, FakeEnvironment, and the sentinel `_drain_until` protocol pass
  (10 passed). Docker integration test written (`tests/integration/`), skips when
  docker is absent.

## Next (in build order)

- **Slice 3** — run the Docker integration test on a host *with Docker* (it was
  written but only unit-verified here; the daemon was unavailable). Confirms the
  full flow on `seed_001`.
- **Slice 4** — `ToolServer` (per sandbox): exposes `open_shell` + `exec(terminal_id, cmd)`
  + `submit` over the `Environment`. (Agent-facing file/edit tool: deferred, see Open questions.)
- **Slice 5** — `Agent` base + a scripted stub agent.
- **Slice 6/7** — `Harness` loop + deterministic test with the stub agent.
- **Slice 8/9** — model-backed `Agent` + one real trial end-to-end.
- Then Phase 2 (Verifier/Logging/Orchestrator/Registry), Phase 3 (Firecracker),
  Phase 4 (SFT -> DPO -> GRPO).

## Open questions / deferred

- **Agent-facing edit tool** — most frameworks have one; deferred. Expect to add
  when eval shows the small model mangling heredoc/`sed` edits.
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
