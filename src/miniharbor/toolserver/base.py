"""The ToolServer: the agent-facing tool layer over one Environment.

One ToolServer per sandbox, the Environment injected on init (the worker owns the
env's lifecycle; the ToolServer only uses it). Exposes a small typed tool surface
the agent calls; each tool dispatches to an Environment method.

Versioned like the harness: the tool surface is pinned per run (one version shared
across all sandboxes in a run) and recorded in the trajectory, because changing it
changes the agent's action space and the training distribution.

Error policy (the load-bearing bit):
  * model error  (unknown tool, bad/missing args) -> a recoverable error Observation,
    so the agent can see it and retry; the trial continues.
  * infra error  (SandboxError from the env) -> propagates, so the worker marks the
    trial infra_failed. Never swallowed as a normal observation.
"""

from __future__ import annotations

from ..environment.base import Environment, SandboxError
from ..models import Observation, ToolSchema

TOOLSERVER_VERSION = "v1"
DEFAULT_MAX_OBS_BYTES = 10_000


class ToolServer:
    version = TOOLSERVER_VERSION

    def __init__(self, env: Environment, *, max_obs_bytes: int = DEFAULT_MAX_OBS_BYTES):
        self._env = env
        self._max = max_obs_bytes
        self._default_terminal: str | None = None      # lazily opened on first exec
        self._handlers = {
            "exec": self._exec,
            "open_shell": self._open_shell,
            "read_file": self._read_file,
            "write_file": self._write_file,
            "submit": self._submit,
        }

    # --- public API -----------------------------------------------------

    def tool_schemas(self) -> list[ToolSchema]:
        return [
            ToolSchema(
                name="exec",
                description=(
                    "Run a shell command in a persistent terminal and return its output. "
                    "Omit terminal_id to use the default session; state (cwd, env vars, "
                    "activated venvs) persists across exec calls."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "description": "the shell command to run"},
                        "terminal_id": {"type": "string", "description": "optional terminal from open_shell"},
                    },
                    "required": ["command"],
                },
            ),
            ToolSchema(
                name="open_shell",
                description=(
                    "Open a new persistent terminal; returns a terminal_id to pass to exec. "
                    "Use for a second concurrent session (e.g. a server in one, a client in another)."
                ),
                parameters={"type": "object", "properties": {}, "required": []},
            ),
            ToolSchema(
                name="read_file",
                description="Read a file from the sandbox.",
                parameters={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            ),
            ToolSchema(
                name="write_file",
                description="Write (create or overwrite) a file in the sandbox.",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"],
                },
            ),
            ToolSchema(
                name="submit",
                description="Signal that the task is complete and end the trial.",
                parameters={"type": "object", "properties": {}, "required": []},
            ),
        ]

    async def call(self, name: str, args: dict | None = None) -> Observation:
        handler = self._handlers.get(name)
        if handler is None:
            return self._error(name, f"unknown tool {name!r}")
        try:
            return await handler(args or {})
        except SandboxError:
            raise                                       # infra -> propagate (worker: infra_failed)
        except (KeyError, TypeError, ValueError) as exc:
            return self._error(name, f"bad arguments: {exc}")   # model error -> recoverable

    # --- handlers -------------------------------------------------------

    async def _exec(self, args: dict) -> Observation:
        cmd = args["command"]
        # Agent exec always runs in a PERSISTENT terminal (never the env's one-shot,
        # which is reserved for the verifier/infra). Default session opened lazily.
        tid = args.get("terminal_id") or await self._ensure_default_terminal()
        res = await self._env.exec(cmd, terminal_id=tid)
        out, truncated, omitted = self._truncate(res.stdout)
        return Observation(
            tool="exec",
            truncated=truncated,
            bytes_omitted=omitted,
            result={
                "stdout": out,
                "exit_code": res.exit_code,
                "timed_out": res.timed_out,
                "duration_ms": res.duration_ms,
                "terminal_id": tid,
            },
        )

    async def _open_shell(self, args: dict) -> Observation:
        tid = await self._env.open_shell()
        return Observation(tool="open_shell", result={"terminal_id": tid})

    async def _read_file(self, args: dict) -> Observation:
        path = args["path"]
        data = await self._env.read_file(path, max_bytes=self._max)
        # env capped at max_bytes; a full-length read is (likely) truncated.
        return Observation(
            tool="read_file",
            truncated=len(data) >= self._max,
            result={"path": path, "content": data.decode(errors="replace")},
        )

    async def _write_file(self, args: dict) -> Observation:
        path, content = args["path"], args["content"]
        await self._env.write_file(path, content.encode())
        return Observation(tool="write_file", result={"ok": True, "path": path})

    async def _submit(self, args: dict) -> Observation:
        # The harness intercepts submit to halt the loop; this is a no-op marker so
        # the tool is callable and appears in tool_schemas().
        return Observation(tool="submit", result={"submitted": True})

    # --- helpers --------------------------------------------------------

    async def _ensure_default_terminal(self) -> str:
        if self._default_terminal is None:
            self._default_terminal = await self._env.open_shell()
        return self._default_terminal

    def _truncate(self, text: str) -> tuple[str, bool, int]:
        if len(text) <= self._max:
            return text, False, 0
        half = self._max // 2
        omitted = len(text) - self._max
        clipped = text[:half] + f"\n...[truncated {omitted} bytes]...\n" + text[-half:]
        return clipped, True, omitted

    @staticmethod
    def _error(tool: str, msg: str) -> Observation:
        return Observation(tool=tool, result={"error": msg})
