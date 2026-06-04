"""An in-memory Environment for unit-testing the layers above it (ToolServer,
Harness) without Docker. It is NOT a sandbox -- it isolates nothing -- it just
satisfies the interface deterministically.

`exec` is driven by a `responses` map (cmd -> ExecResult) with a default; the
filesystem is a dict. This lets a test script the sandbox's behavior exactly.
"""

from __future__ import annotations

from ..models import ExecResult
from .base import Environment


class FakeEnvironment(Environment):
    def __init__(self, responses: dict[str, ExecResult] | None = None):
        self._responses = responses or {}
        self._fs: dict[str, bytes] = {}
        self._procs: dict[str, str] = {}
        self._started = False
        self._proc_seq = 0

    async def start(self) -> None:
        self._started = True

    async def destroy(self) -> None:
        self._started = False

    async def exec(self, cmd, *, cwd="/workspace", timeout_s=30, env=None) -> ExecResult:
        if cmd in self._responses:
            return self._responses[cmd]
        return ExecResult(stdout="", stderr="", exit_code=0, timed_out=False, duration_ms=0)

    async def read_file(self, path, *, max_bytes=10_000) -> bytes:
        return self._fs.get(path, b"")[:max_bytes]

    async def write_file(self, path, content) -> None:
        self._fs[path] = content

    async def start_process(self, cmd, *, cwd="/workspace") -> str:
        self._proc_seq += 1
        pid = f"proc_{self._proc_seq}"
        self._procs[pid] = cmd
        return pid

    async def read_process_output(self, process_id, *, max_bytes=10_000) -> str:
        return ""

    async def stop_process(self, process_id) -> None:
        self._procs.pop(process_id, None)

    async def snapshot(self) -> str:
        return "fake-snapshot"
