# Task bundles

A task is what a task author (or a synthetic generator) ships. It is a directory
with a fixed layout. This doc is the authoritative description of that layout and
of the agent-visibility boundary; the schema lives in
[`../docs/data-model.md`](../docs/data-model.md) §1 and the verifier contract in
[`../docs/components/verifier.md`](../docs/components/verifier.md).

Examples: [`logfixbench/seed_001`](logfixbench/seed_001) (events dropped during
batch processing) and [`logfixbench/seed_002`](logfixbench/seed_002) (duplicate
writes under at-least-once delivery). Same family, same structure, different
injected bug — the shape a generator emits.

## Layout

```
<family>/<seed>/
├── task.toml          metadata, resources, budgets, verifier contract
├── instruction.md     the prompt shown to the agent (and nothing else is)
├── environment/
│   └── Dockerfile     image definition; built with context = bundle root
├── workspace/         initial state baked at /workspace (code + PUBLIC tests)
│   ├── *.py
│   ├── tests_public.py
│   └── README.md
├── tests/             HIDDEN verifier — never baked into the agent's image
│   ├── run.sh         standardized entrypoint; writes the reward file
│   └── test_hidden.py the authoritative grader
└── solution/          reference solution, used only for task validation
    └── solution.patch
```

## The agent-visibility boundary (the crux)

| Path | Baked into the agent's sandbox image? | Why |
|---|---|---|
| `instruction.md` | shown as the prompt | the task statement |
| `workspace/` (incl. `tests_public.py`) | yes | the code to work on + a repro the agent may run |
| `tests/` (hidden) | **no** | if the agent could read the grader, it would game it |
| `solution/` | **no** | reference answer; validation only |

The image is built from `environment/Dockerfile` with `COPY workspace/ /workspace/`.
It deliberately does **not** include `tests/` or `solution/`.

## The verifier flow (how `tests/` reaches the sandbox)

After the agent halts, the worker grades like this:

```
1. stop the agent's processes (freeze /workspace)
2. copy the bundle's tests/ INTO the sandbox at task.toml [verifier].inject_path
   (e.g. /opt/verifier) — a path the agent was never told about
3. run the entrypoint there:  bash /opt/verifier/run.sh
4. run.sh runs test_hidden.py against /workspace and writes the reward to
   task.toml [verifier].reward_path  (/logs/verifier/reward.json)
5. the worker reads reward.json -> Reward {reward, passed, breakdown}
```

`run.sh`'s exit code is ignored on purpose; the **reward file** is the
authoritative result. The hidden test imports the agent's (possibly modified)
code from `/workspace`, so it grades exactly what the agent left behind.

In the v1 Docker adapter this runs in the frozen container; the production swap
runs it in an isolated grader booted from a snapshot of `/workspace` (see
[`../docs/components/verifier.md`](../docs/components/verifier.md)). The bundle
does not change.

## The reward contract

`run.sh` must write JSON matching `Reward`:

```json
{"reward": 1.0, "passed": true, "breakdown": {"passed": 8, "total": 8}}
```

`reward` is `0.0..1.0` (here, fraction of hidden tests passed, so partial credit
falls out for free); `passed` is the pass/fail summary; `breakdown` is optional
detail. The harness parses only this file — how it was produced (pytest, a
script, a binary) is opaque.

## Validation gates (a task is only usable if it passes)

Before a task enters the registry as `ready` (see
[`../docs/components/registry.md`](../docs/components/registry.md)):

| Gate | Check | Command (from a copy of `workspace/`) |
|---|---|---|
| builds | image builds | `docker build -f environment/Dockerfile -t t .` |
| solvable | reference solution → reward = pass | `patch -p1 < solution/solution.patch` then run the verifier |
| non-trivial | unmodified workspace → reward = fail | run the verifier on the as-shipped workspace |
| deterministic | verifier twice → identical reward | run the verifier twice on identical state |

`solution.patch` applies from inside `/workspace` with `patch -p1`.

## The format is runtime-agnostic (this is the point)

The example tasks happen to be Python bug-fixes, but nothing in the format or
the harness assumes Python, pytest, or any language. The harness makes exactly
three assumptions about a task, all language-neutral:

1. **`environment/` is an arbitrary OCI image.** The harness only does
   `build` → `run` → `exec`. It never parses the Dockerfile or assumes its
   contents. The image can be a Go repo, a Rust toolchain, a C build, a Node
   service, or a full benchmark repo with a conda env.
2. **The agent acts only through `bash`/fs/process tools** — language-neutral.
   Inside the sandbox the agent has full freedom to do whatever the task needs.
3. **The verifier is an opaque entrypoint + a reward file.** `entrypoint` can be
   `pytest`, `go test`, `make check`, a shell script, or any benchmark's own
   evaluation harness. The harness runs it and reads `reward.json`; it does not
   care how the number was produced.

So the only harness-imposed contract is: an image, an instruction string, and a
verifier entrypoint that writes a reward file. Everything else is the task's
business.

### Supporting Terminal-Bench / SWE-bench tasks

Arbitrary Terminal-Bench or SWE-bench tasks are supported via an **adapter** that
maps their fields onto this bundle layout (the same approach as Harbor's
`adapters/`):

| Bundle field | Terminal-Bench | SWE-bench instance |
|---|---|---|
| `environment/Dockerfile` | the task Dockerfile | base image: repo cloned at `base_commit` + deps |
| `instruction.md` | the task prompt | `problem_statement` |
| `workspace/` | task files | the repo working tree |
| `tests/` entrypoint + `reward_path` | the test harness | apply `test_patch`, run `FAIL_TO_PASS` + `PASS_TO_PASS`; reward = 1 iff all pass |
| `solution/` | reference solution | the golden `patch` |

Two operational notes: build-time network is fine (the build is a separate step
before the agent runs); runtime egress is off by default, with an explicit
allowlist exception for tasks that genuinely need it.

## Building one by hand

```
cd <family>/<seed>
docker build -f environment/Dockerfile -t logfixbench-seed-001 .
docker run -d --network none --name t logfixbench-seed-001
docker exec t python -m pytest tests_public.py -q     # fails: the bug is present
```
