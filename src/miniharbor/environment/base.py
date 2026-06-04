"""The Environment interface.

An Environment is a generic, disposable sandbox for one trial. It knows ONLY
filesystem + exec + lifecycle. It does not know about tasks' grading, the agent,
the ToolServer/MCP, image building, or artifact storage -- those all live above
or beside it. That ignorance is what lets the SAME interface be implemented by
Docker, a microVM, or a hosted backend.

It is an abc.ABC (not a typing.Protocol) on purpose: we own every implementation,
we want construction-time enforcement of the method set, and we want to share the
async-context-manager helper. Protocol would be right only if we were duck-typing
types we don't control.
"""

from __future__ import annotations

import abc

from ..models import ExecResult


class SandboxError(RuntimeError):
    """An infra-level sandbox failure: the daemon/hypervisor failed, the sandbox
    could not start, or a control operation failed.

    This is distinct from a command exiting nonzero (a normal ExecResult). The
    caller maps SandboxError to an `infra_failed` trial -- retryable, NOT counted
    against the model. A nonzero exit code is a model result and is NEVER an error.
    """


class Environment(abc.ABC):
    """One disposable sandbox. Implementations: DockerEnvironment, etc.

    Concrete subclasses are constructed with the slice of a resolved `Task` they
    need (`image_ref`, `resources`, `network`, `workdir`) -- they do not take a
    raw task_id and do not depend on the Registry.

    Lifecycle: start() -> (exec / read / write / process tools)* -> snapshot()? -> destroy().
    Use as an async context manager to guarantee teardown:

        async with DockerEnvironment(task) as env:
            await env.exec("pytest -q")
        # destroy() runs even on exception
    """

    # --- lifecycle -------------------------------------------------------

    @abc.abstractmethod
    async def start(self) -> None:
        """Boot the sandbox from the (already-built) image. Egress off by default.

        Raises SandboxError if the sandbox cannot be created.
        """

    @abc.abstractmethod
    async def destroy(self) -> None:
        """Tear the sandbox down and release all resources. Idempotent; must be
        safe to call after a failed start or twice. Never raises on a missing
        sandbox -- cleanup must always succeed.
        """

    # --- command execution ----------------------------------------------

    @abc.abstractmethod
    async def exec(
        self,
        cmd: str,
        *,
        cwd: str = "/workspace",
        timeout_s: int = 30,
        env: dict[str, str] | None = None,
    ) -> ExecResult:
        """Run a single shell command to completion inside the sandbox.

        `cmd` is interpreted by a shell INSIDE the sandbox (the agent's bash tool);
        the host shell is never involved. Returns an ExecResult for any command
        that ran, including a nonzero exit. On wall-clock timeout, returns
        ExecResult(timed_out=True, exit_code=124). Raises SandboxError only on an
        infra failure (e.g. the sandbox is gone).
        """

    # --- filesystem ------------------------------------------------------

    @abc.abstractmethod
    async def read_file(self, path: str, *, max_bytes: int = 10_000) -> bytes:
        """Read up to `max_bytes` from `path` in the sandbox. Binary-safe."""

    @abc.abstractmethod
    async def write_file(self, path: str, content: bytes) -> None:
        """Write `content` to `path` in the sandbox, creating parent dirs.
        Binary-safe (no shell escaping of the payload; no arg-length limit).
        """

    # --- long-running processes -----------------------------------------
    # For work that outlives a single exec (a dev server, a watcher). Avoids
    # shell-backgrounding hacks and tmux; the agent gets a handle it can poll/stop.

    @abc.abstractmethod
    async def start_process(self, cmd: str, *, cwd: str = "/workspace") -> str:
        """Start `cmd` in the background; return an opaque process_id."""

    @abc.abstractmethod
    async def read_process_output(self, process_id: str, *, max_bytes: int = 10_000) -> str:
        """Return up to the last `max_bytes` of the process's combined output."""

    @abc.abstractmethod
    async def stop_process(self, process_id: str) -> None:
        """Stop the process. Idempotent; safe if already exited."""

    # --- snapshot --------------------------------------------------------

    @abc.abstractmethod
    async def snapshot(self) -> str:
        """Capture the final state and return an opaque snapshot ref.

        The Environment only PRODUCES the snapshot (a ref/bytes). Persisting it
        (to an ArtifactStore) is the caller's job -- the sandbox is not coupled to
        any storage backend.
        """

    # --- async context manager (shared, concrete) ------------------------

    async def __aenter__(self) -> "Environment":
        await self.start()
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self.destroy()
