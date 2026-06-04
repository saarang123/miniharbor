# Industry comparison (living doc)

How each MiniHarbor design decision compares to real frameworks, so the design
is always legible against what's standard. Updated as decisions are made.

Reference points:
- **Harbor** — harbor-framework/harbor (Terminal-Bench team): the framework MiniHarbor is a study build of.
- **SWE-agent** — research agent + ACI (Agent-Computer Interface) for SWE-bench.
- **mini-swe-agent** — the deliberately minimal (~100-line) SWE-agent variant.
- **Claude Code** — Anthropic's coding agent (Bash + Read/Write/Edit tools).
- **Terminal-Bench** — terminal-task benchmark + harness.
- **E2B / Modal** — sandbox-as-a-service.

Legend: ✅ same as us · ≈ close · ✗ differs.

---

## 1. Sandbox isolation

| | Approach |
|---|---|
| **MiniHarbor** | Docker v1 (trusted-author tasks) → microVM (Firecracker) for untrusted; behind one `Environment` interface |
| Harbor | swappable providers: Docker, Daytona, E2B, Modal, Runloop (microVM/gVisor under the hood) ✅ |
| SWE-agent | Docker ≈ |
| Terminal-Bench | Docker ≈ |
| E2B / Modal | Firecracker / gVisor microVMs ✅ (the production end-state) |

**Where we sit:** standard. Docker-for-dev, microVM-for-untrusted is the consensus; the swappable-provider interface is exactly Harbor's `BaseEnvironment`.

## 2. Agent ↔ sandbox execution

| | Approach |
|---|---|
| **MiniHarbor** | one-shot `exec` (verifier/infra) + persistent terminal via held-open `docker exec -i bash` with a per-command **sentinel** for done/exit-code |
| SWE-agent | persistent shell + `communicate()` with an echoed sentinel + `$?` ✅ (we independently matched this) |
| Claude Code | persistent bash session (cwd persists) + background-process tool ≈ |
| mini-swe-agent | stateless `subprocess.run` per action (no session) ✗ (simpler; proves stateless works for SWE-bench) |
| Terminal-Bench | **tmux** pane, sends keystrokes, scrapes the pane ✗ (real TTY; we rejected tmux for leak/ANSI/done-detection reasons) |
| E2B / Modal | `commands.run()` one-shot + a separate `pty` API ≈ |

**Where we sit:** our terminal mechanism is SWE-agent's. The one-shot+session split mirrors E2B. We diverge from Terminal-Bench's tmux deliberately; the cost is no real-TTY fidelity (interactive ncurses/REPL detection) until the PTY path lands.

## 3. Loop ownership

| | Approach |
|---|---|
| **MiniHarbor** | harness-owned (agent is a blind policy) → clean (obs, action) pairs for training |
| mini-swe-agent / SWE-agent | harness-owned ✅ |
| Harbor | mostly wraps **external** agents (claude-code, opencode), i.e. agent-owned, capturing the trajectory ✗ (Harbor targets eval of many agents; we target training one policy) |

**Where we sit:** harness-owned because the endgame is training; Harbor is agent-owned because it benchmarks third-party agents. Both legitimate; different goals.

## 4. File operations

| | Approach |
|---|---|
| **MiniHarbor** | `read_file`/`write_file` at the **env level** (verifier/infra use); agent-facing edit tool **TBD** |
| Claude Code | dedicated `Read`/`Write`/`Edit` (str_replace) ✅ |
| SWE-agent | dedicated ACI `open`/`edit` with lint feedback ✅ |
| Anthropic computer-use | `text_editor` tool (view/create/str_replace/insert) ✅ |
| E2B | filesystem API (read/write/list) ✅ |
| mini-swe-agent | bash-only (`cat`/heredoc) ✗ (minimal; a known accuracy tradeoff) |

**Where we sit:** the field overwhelmingly uses dedicated file/edit tools because models botch heredoc/sed edits (SWE-agent's ACI paper quantifies the gain). Open question for us: whether to expose an agent-facing edit tool. Expectation: needed for a small model (Qwen3-4B); start minimal, add when eval shows edit failures.

## 5. Exec result semantics

| | Approach |
|---|---|
| **MiniHarbor** | nonzero exit = normal `ExecResult`; timeout → `timed_out=True`/124; only infra failure raises `SandboxError`; timeout **always bounded** |
| General (SWE-agent, E2B, Claude Code) | same shape: structured result, exit code returned, errors ≠ nonzero ✅ |

**Where we sit:** standard. The infra-vs-model distinction (raise vs return) is the part most home-grown harnesses get wrong; we encode it in the type.

## 6. Verifier / reward contract

| | Approach |
|---|---|
| **MiniHarbor** | run `entrypoint`; read reward from `/logs/verifier/reward.json` |
| Harbor | reward read from `/logs/verifier/reward.txt` or `.json` ✅ (copied exactly) |
| SWE-bench | apply `test_patch`, run `FAIL_TO_PASS`/`PASS_TO_PASS`; resolved iff all pass ≈ (maps via adapter) |

**Where we sit:** identical to Harbor; SWE-bench maps in via an adapter.

## 7. Build vs run

| | Approach |
|---|---|
| **MiniHarbor** | `build_image()` separate from the env; env boots an already-built `image_ref` |
| Harbor / SWE-bench / Terminal-Bench | image built once, runs referenced by tag/digest ✅ |

**Where we sit:** standard; keeping build out of the env is what preserves Docker↔microVM portability.

## 8. Trajectory format

| | Approach |
|---|---|
| **MiniHarbor** | ATIF-compatible trajectory (both halves of each step) |
| Harbor | ATIF (Agent Trajectory Interchange Format) ✅ |

**Where we sit:** aligned with Harbor's interchange format so trajectories feed SkyRL/GEPA-style training.

---

## Net read

MiniHarbor sits squarely in the mainstream: the sandbox interface is Harbor's, the persistent terminal is SWE-agent's, the reward contract is Harbor's, the trajectory is ATIF. The deliberate divergences are: harness-owned loop (vs Harbor's agent-wrapping — because we train), and no tmux (vs Terminal-Bench — pipe+sentinel instead, PTY deferred). The one open gap vs the field is an agent-facing edit tool, which most frameworks have and we've deferred.
