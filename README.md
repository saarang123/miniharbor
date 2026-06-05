# MiniHarbor

A small, correct, from-scratch agent-evaluation and RL-environment runner. Point an agent at a containerized task, run it many times in isolated sandboxes, grade each run with a hidden verifier, and emit either an **eval report** or **RL rollout data** that feeds post-training of an open model.

MiniHarbor is a study build of the [Harbor](https://github.com/harbor-framework/harbor) framework (agent evals + RL environments, from the Terminal-Bench team). The goal is to understand and own every building block of a real sandboxed eval loop — not to ship production infrastructure. It is small at every layer, but the *shape* is production-shaped: each subsystem is an interface with a small-scale adapter today and a documented path to the production adapter.

## The loop

```
task  ──►  isolated sandbox  ──►  harness-owned agent loop  ──►  hidden verifier  ──►  reward + full trajectory
          (disposable,            (observe → act → observe,       (runs against         (one record = an eval
           egress off)             budgets enforced)               final state)          result AND an RL example)
                                                                        │
                          held-out eval  ◄──  improved model  ◄──  post-train on good trajectories
```

An eval result and an RL training example are the same object viewed two ways. That equivalence is the core idea.

## Components

Each is an interface (a "port") with swappable adapters. Callers depend only on the interface.

| Module | Responsibility | v1 adapter | Documented swap-to |
|---|---|---|---|
| [Harness](docs/components/harness.md) | The fixed, **versioned** observe→act loop; owns budgets and step logging. Owns the loop. | the loop | tune the scaffold, never the contract |
| [Agent](docs/components/agent.md) | Policy: "given history → next action." Swappable model client + prompt template + parser. | model-backed ReAct policy | wrap external CLI agents (eval-only) |
| [Environment](docs/components/environment.md) | Sandbox lifecycle + `exec`/fs/`snapshot`/`destroy`. Takes an image spec, sets up a sandbox. | Docker | Firecracker microVM, Kata, gVisor, hosted (E2B/Modal) |
| [ToolServer](docs/components/tool-server.md) | MCP server running against the environment; exposes `bash`/`read_file`/`write_file`/process tools. | typed/MCP local | MCP over vsock |
| [Verifier](docs/components/verifier.md) | Anything with two parts: a standardized run command + a reward written to a file. | in-sandbox runner | isolated grader context |
| [Logging](docs/components/logging.md) | **Versioned** trajectory logger wired into the harness; emits an ATIF-compatible record. | JSONL | object store; direct feed to post-training |
| [Orchestrator](docs/components/orchestrator.md) | Expand a job into trials, queue, schedule onto workers, collect results. | FIFO + concurrency cap | bin-packing / cell-based scheduler |
| [Registry](docs/components/registry.md) | Task/dataset catalog; on-disk task bundle format. | local task dir | registered `<org/name@version>` |
| [Post-training](docs/pipeline/posttraining.md) | Trajectories → training data → LoRA SFT → DPO → GRPO → held-out re-eval. | offline SFT | online RL with model server in the loop |

## Repo layout

```
miniharbor/
├── README.md
├── docs/
│   ├── architecture.md         design philosophy, layering, control flow, swap matrix, isolation model
│   ├── data-model.md           Task / Job / Trial / Trajectory (ATIF) / Reward schemas + status taxonomy
│   ├── components/             one doc per module (the table above)
│   ├── pipeline/posttraining.md
│   ├── build-order.md          dependency-ordered slices
│   ├── design-notes.md         cross-cutting principles + lessons earned from live runs
│   └── industry-comparison.md  how each decision compares to Harbor / SWE-agent / E2B / ...
└── (impl lands later, under src/)
```

## Status

Design phase. Docs describe the target design and the v1-vs-swap split per module. Implementation follows the docs.

## How to read

1. [`docs/architecture.md`](docs/architecture.md) — the whole-system view, the ports-and-adapters discipline, and the control flow of one trial.
2. [`docs/data-model.md`](docs/data-model.md) — the data contracts (Task on disk, Trial lifecycle, the trajectory/reward schemas) that every component shares.
3. The component docs, in dependency order: Environment → ToolServer → Agent → Harness → Verifier → Logging → Orchestrator → Registry → Post-training.
