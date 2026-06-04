# ToolServer

The agent-facing abstraction. An MCP server bound to an `Environment` that exposes a small, typed set of tools (`bash`, `read_file`, `write_file`, process control, `submit`). The agent issues structured tool calls; the ToolServer translates each into environment operations and returns a structured observation. The tool *schema* is stable; the *transport* is swappable.

> Port. v1 adapter: local typed/MCP server in-process with the harness. Swap-to: MCP over vsock to a guest agent inside a microVM.

## Why a typed tool API and not a raw shell

The agent never gets raw host access, a real TTY, or a screen to scrape. It gets a fixed schema of tools with typed arguments and typed results. This makes the action space well-defined (so trajectories are parseable and trainable), keeps the agent decoupled from the backend (Docker vs microVM is invisible to the agent), and gives the harness a single place to enforce per-call timeouts and to log every call.

## Interface

```python
class ToolSchema(BaseModel):
    name: str
    description: str
    parameters: dict          # JSON Schema for the args

class Observation(BaseModel):
    tool: str
    result: dict
    truncated: bool = False
    bytes_omitted: int = 0

class ToolServer(Protocol):
    def tool_schemas(self) -> list[ToolSchema]: ...
    async def call(self, name: str, args: dict) -> Observation: ...
```

A ToolServer is constructed bound to an `Environment`; each tool dispatches to one or more environment methods.

## The tool set

```python
bash(command: str, cwd: str = "/workspace", timeout_s: int = 30, env: dict = {})
    → {stdout, stderr, exit_code, timed_out, duration_ms}      # env.exec

read_file(path: str, max_bytes: int = 10_000)
    → {content, truncated, bytes_omitted}                       # env.read_file

write_file(path: str, content: str)
    → {ok: bool}                                                # env.write_file

list_dir(path: str = "/workspace")
    → {entries: [...]}                                          # env.exec ls

start_process(command: str, cwd: str = "/workspace")
    → {process_id}                                              # env.start_process

read_process_output(process_id: str, max_bytes: int = 10_000)
    → {output, truncated}                                       # env.read_process_output

stop_process(process_id: str)
    → {ok: bool}                                                # env.stop_process

submit()
    → terminal: signals the harness the agent is done             # no env call
```

`submit()` is the agent's "done" action; it halts the harness loop. Everything else maps onto the `Environment` interface.

## Why stateless bash plus process tools, not tmux

A stateless `bash(command, cwd, timeout)` is deterministic and easy to log: one call, one structured result, no ANSI parsing, no done-detection race. The failure mode of a persistent shell over tmux is that the harness depends on the same multiplexer a task might itself use — the abstraction leaks. Long-running work (a dev server, a watcher) uses the process tools instead of shell backgrounding hacks:

```
start_process("npm run dev")      → proc_123
bash("curl -s localhost:3000/health")
read_process_output("proc_123")   → recent logs
stop_process("proc_123")
```

The evolution path: v1 stateless `bash` + process tools; later a raw PTY-backed shell managed by the guest agent if a task genuinely needs interactivity.

## Observation formatting

The ToolServer is responsible for keeping observations bounded. Raw command output can be large; the server truncates to a cap (head+tail with a marker), sets `truncated` and `bytes_omitted`, and returns structured fields. This formatting is part of what the harness version pins, because how observations are truncated changes the model's input and therefore the training distribution.

## Transport: v1 vs vsock

- **v1 (in-process / local MCP):** the ToolServer runs in the harness process and calls a local `Environment` (Docker). If exposed as a real MCP server, it speaks MCP over stdio/SSE to the harness acting as MCP client.
- **Swap-to (MCP over vsock):** for a microVM, the tool dispatch crosses the host↔guest boundary over vsock to a guest agent that performs the actual `exec`/fs operations. The guest agent is the sandbox side of this interface (see [`environment.md`](environment.md) Firecracker adapter). The schema the agent sees is unchanged; only the wire under `call()` changes.

## Relationship to the Agent

The ToolServer publishes `tool_schemas()`; the Agent's `ModelClient` is given those schemas so the model can emit native tool calls (or the prompt template renders them as text for a ReAct parser). The harness takes the Agent's `Action`, calls `ToolServer.call(action.tool, action.args)`, and feeds the `Observation` back. The Agent and the Environment never reference each other directly — the ToolServer and harness sit between them.
