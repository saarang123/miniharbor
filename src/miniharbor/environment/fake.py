"""An in-memory Environment for unit-testing the layers above it (ToolServer,
Harness) without Docker. It is NOT a sandbox -- it isolates nothing -- it just
satisfies the interface deterministically.

`exec` is driven by a `responses` map (cmd -> ExecResult) with a default; the
filesystem is a dict; terminals are tracked by id but share the one fake exec.
"""

from __future__ import annotations

import uuid

from ..models import ExecResult
from .base import Environment


class FakeEnvironment(Environment):
    def __init__(self, responses: dict[str, ExecResult] | None = None):
        self._responses = responses or {}
        self._fs: dict[str, bytes] = {}
        self._terminals: set[str] = set()
        self._started = False

    async def start(self) -> None:
        self._started = True

    async def destroy(self) -> None:
        self._terminals.clear()
        self._started = False

    async def exec(self, cmd, *, terminal_id=None, cwd="/workspace", timeout_s=None, env=None) -> ExecResult:
        if cmd in self._responses:
            return self._responses[cmd]
        return ExecResult(stdout="", stderr="", exit_code=0, timed_out=False, duration_ms=0)

    async def open_shell(self) -> str:
        tid = f"term_{uuid.uuid4().hex[:8]}"
        self._terminals.add(tid)
        return tid

    async def close_shell(self, terminal_id) -> None:
        self._terminals.discard(terminal_id)

    async def read_file(self, path, *, max_bytes=10_000) -> bytes:
        return self._fs.get(path, b"")[:max_bytes]

    async def write_file(self, path, content) -> None:
        self._fs[path] = content

    async def snapshot(self) -> str:
        return "fake-snapshot"
