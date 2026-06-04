# Architecture

The whole-system view: the design philosophy, the layering, the control flow of one trial, the swap matrix, and the isolation model. Every component doc conforms to the interfaces named here.

## 1. Design philosophy — ports and adapters

Every subsystem is a **port** (an interface) with one or more **adapters** (implementations). Callers depend on the port, never on a concrete adapter. Swapping an adapter is a dependency-injection change, not a rewrite.

This buys two things at once:
- **Small now.** v1 picks the simplest adapter per port (Docker, SQLite, a FIFO queue, local-filesystem artifacts).
- **Production-shaped.** The port signature is the same one a production system uses, so the small adapter and the production adapter are interchangeable. Learning the small version teaches the real version.

The discipline is a hard rule: if a caller imports a concrete adapter, that is a bug. Adapters are constructed at the edge (a factory / config) and passed in.

## 2. The layering

The single most important structural decision: **the harness owns the loop.** The agent is a pure policy — "given history, produce the next action" — and is blind to everything except the observations the harness feeds it. That blindness is what makes a trajectory a well-defined training example and the agent swappable.

```
Job  ──expand──►  Trial (one task × one attempt)
                    │
                    ▼
   ┌───────────────────────────────────────────────────────┐
   │ Harness  (FIXED, VERSIONED loop; owns budgets + logging)│
   │   loop:                                                 │
   │     observation  ─►  Agent.act(context)  ─►  Action     │
   │     Action       ─►  ToolServer.call()   ─►  Observation│
   │     Logging.on_step(...)                                │
   │     repeat until Submit / budget trip                   │
   │   ┌──────────────────┐        ┌──────────────────────┐  │
   │   │ Agent (policy)   │        │ ToolServer (MCP)     │  │
   │   │ ModelClient +    │        │ tool schema +        │  │
   │   │ PromptTemplate + │        │ dispatch to env      │  │
   │   │ Parser           │        │                      │  │
   │   └──────────────────┘        └──────────┬───────────┘  │
   └───────────────────────────────────────────┼────────────┘
                                                ▼
                                   ┌────────────────────────┐
                                   │ Environment (sandbox)  │
                                   │ start/exec/read/write/ │
                                   │ snapshot/destroy       │
                                   │ adapter: docker | fc | │
                                   │          kata | hosted │
                                   └───────────┬────────────┘
                                               │ on halt
                                               ▼
                                   Verifier ─► Reward ─► Logging.emit() ─► Trajectory (ATIF)
```

Why each layer exists, in one line:

- **Agent** is the unit under test or under training. It must be swappable (different model, prompt, parser) without touching the loop.
- **Harness** is fixed across agents within a run, so two agents are compared under identical conditions. It is **versioned** because changing the scaffold changes both eval comparability and the training-data distribution.
- **ToolServer** is the agent-facing abstraction. The agent issues structured tool calls; the ToolServer translates them into environment operations. The tool *schema* is stable; the transport can change.
- **Environment** is the isolation boundary. The same six-method interface is implemented by Docker, a microVM, or a hosted sandbox. Only the implementation changes.
- **Verifier** computes the reward from the final state, decoupled from the harness by a file contract.
- **Logging** records every step as it happens and emits the trajectory in a format that flows directly into post-training.

### Loop ownership: two modes

| Mode | Who drives | Use |
|---|---|---|
| **Harness-owned** (v1) | The harness asks the agent for one action, executes it, feeds the result back. Clean `(observation, action)` pairs. | Training your own policy; apples-to-apples eval of your own model. |
| **Agent-owned** (later) | An external agent runs its own loop and calls the ToolServer directly; the harness provides the environment and captures whatever trajectory the agent exposes. | Benchmarking third-party CLI agents you do not control or train. |

v1 is harness-owned because the endgame is training an open model. Agent-owned is a later adapter behind the same `Agent` seam.

## 3. Core interfaces

Python, async-first, Pydantic v2 models, factory-constructed adapters. These signatures are the contract; component docs expand each.

```python
class ExecResult(BaseModel):
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool
    duration_ms: int

class Environment(Protocol):
    async def start(self) -> None: ...
    async def exec(self, cmd: str, cwd: str = "/workspace",
                   timeout_s: int = 30, env: dict[str, str] | None = None) -> ExecResult: ...
    async def read_file(self, path: str, max_bytes: int = 10_000) -> bytes: ...
    async def write_file(self, path: str, content: bytes) -> None: ...
    async def start_process(self, cmd: str, cwd: str = "/workspace") -> str: ...   # returns process_id
    async def read_process_output(self, process_id: str, max_bytes: int = 10_000) -> str: ...
    async def stop_process(self, process_id: str) -> None: ...
    async def snapshot(self) -> str: ...                                            # returns snapshot ref
    async def destroy(self) -> None: ...

class ModelClient(Protocol):
    async def complete(self, messages: list[Message], tools: list[ToolSchema],
                       **kwargs) -> ModelResponse: ...

class Agent(Protocol):
    name: str
    version: str
    async def act(self, context: TrajectoryContext) -> Action: ...

class ToolServer(Protocol):
    def tool_schemas(self) -> list[ToolSchema]: ...
    async def call(self, name: str, args: dict) -> Observation: ...

class Verifier(Protocol):
    async def verify(self, env: Environment, task: Task) -> Reward: ...

class TrajectoryLogger(Protocol):
    version: str
    def on_trial_start(self, trial: TrialSpec) -> None: ...
    def on_step(self, step: Step) -> None: ...
    def on_trial_end(self, result: TrialResult) -> None: ...
    def emit(self) -> Trajectory: ...

class Scheduler(Protocol):
    async def submit(self, trials: list[TrialSpec]) -> None: ...
    async def claim(self, worker: WorkerCtx) -> TrialSpec | None: ...

class Store(Protocol): ...          # metadata: jobs, trials, results
class ArtifactStore(Protocol): ...  # trajectories, snapshots, diffs
```

The data models (`Task`, `Job`, `TrialSpec`, `TrialResult`, `Action`, `Observation`, `Step`, `Trajectory`, `Reward`) are defined in [`data-model.md`](data-model.md).

## 4. Control flow of one trial

```
1. Worker claims a TrialSpec {trial_id, image_ref, agent_cfg, model, budgets, verifier_ref}
2. Environment.start()                       # boot sandbox from image_ref; egress off
3. ToolServer bound to the Environment       # tool schema exposed to the agent
4. Harness.run(task, env):
     context = initial observation (instruction + workspace listing)
     loop, until Submit action or a budget trips:
       action      = Agent.act(context)      # model decides
       observation = ToolServer.call(action) # executed in the sandbox
       Logging.on_step(action, observation, model_io, tokens, timing)
       context.append(observation)
5. Verifier.verify(env, task)                # run hidden tests → Reward (from reward file)
6. Environment.snapshot()                     # final state → artifact store
7. Logging.emit() → Trajectory (ATIF)         # + Reward + metrics → Store + ArtifactStore
8. Environment.destroy()
```

Budgets enforced by the harness: max steps, wall-clock, max tokens. A budget trip halts the loop and sets the trial status accordingly (see the status taxonomy in [`data-model.md`](data-model.md)).

## 5. The swap matrix

What each port runs at v1 and what it graduates to. The point of the build is that each row is a single, self-contained upgrade behind a stable interface.

| Port | v1 adapter | Swap-to |
|---|---|---|
| Environment | Docker (`runc`) | Firecracker microVM (own the rootfs/kernel/vsock build); Kata (OCI-compatible, automatic image port); gVisor (`runsc`); hosted E2B/Modal |
| ToolServer | local typed/MCP server | MCP over vsock to a guest agent |
| ModelClient | OpenAI-compatible HTTP | self-hosted batched server with logprobs for RL |
| Store | SQLite | Postgres → analytics store for trial metrics |
| Queue/Scheduler | FIFO + concurrency cap (`SELECT ... FOR UPDATE SKIP LOCKED`) | Redis/Kafka + bin-packing / cell-based |
| ArtifactStore | local filesystem | object store (S3-compatible) with TTL |
| Verifier run-context | in the frozen sandbox | isolated grader sandbox |
| Logging sink | JSONL files | object store; streamed to the trainer |
| TrainerBackend | LoRA SFT (offline) | DPO → GRPO/RLVR (online, model server in loop) |

## 6. The isolation model (abstract)

The sandbox runs two kinds of untrusted code: the **task's build** (the image definition) and the **agent's runtime actions** (arbitrary commands). The isolation question is "does that code reach the host kernel directly?"

| Rung | Boundary the untrusted code must break | Direct host-syscall surface | Fit for hostile agent code |
|---|---|---|---|
| Container (`runc`) | host kernel | yes | no — trusted code only |
| gVisor (`runsc`) | user-space kernel (Sentry), then small host syscall set | mostly no | acceptable |
| microVM (Firecracker) | guest kernel, then VMM/KVM | no | yes |
| full VM (QEMU) | guest kernel, then larger device surface | no | strong but heavy |

v1 uses Docker because the task author is trusted (single-author tasks). The microVM adapter is the real-isolation target and the reason the Environment interface is six methods wide and no wider — `exec`/fs/`snapshot`/`destroy` are exactly what both `docker exec` and a `vsock → guest-agent` transport can provide. See [`environment.md`](components/environment.md) for the OCI-image-to-microVM port mechanism.

Egress policy is part of the Environment: Docker uses `--network none`; a microVM simply attaches no network device. Snapshotting is part of the Environment too: Docker exports an overlay diff; a microVM uses a native memory+disk snapshot with on-demand-paging restore (the better primitive for inspecting a trial's final state long after it ran).

## 7. Naming alignment with Harbor

Where MiniHarbor mirrors Harbor's vocabulary so the learning transfers: `Environment` (swappable providers), `Agent`, `Verifier` (reward read from a file under `/logs/verifier/`), `Dataset`/`Registry`, `Job` (trials across agents × tasks × attempts), `Trial` (one execution), and **ATIF** (Agent Trajectory Interchange Format) as the trajectory output that feeds RL/optimization frameworks. The on-disk task bundle format and the verifier reward-file contract are copied directly; they are the interop boundaries.

The one place MiniHarbor extends past a Harbor clone: closing the **ATIF → post-training → re-eval** loop end-to-end, rather than emitting ATIF and handing off.
