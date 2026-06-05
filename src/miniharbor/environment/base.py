"""The Environment interface.

An Environment is a generic, disposable sandbox for one trial. It knows ONLY
filesystem + command execution + lifecycle. It does not know about tasks' grading,
the agent, the ToolServer/MCP, image building, or artifact storage -- those all
live above or beside it. That ignorance is what lets the SAME interface be
implemented by Docker, a microVM, or a hosted backend.

Two execution modes, one method:
  * exec(cmd, terminal_id=None)  -> a ONE-SHOT command (fresh shell, clean kill on
    timeout). Used by the verifier and trial setup -- isolated from the agent.
  * exec(cmd, terminal_id="t1")  -> runs in a PERSISTENT terminal opened via
    open_shell(); cwd / env vars / activated venvs persist across calls.

It is an abc.ABC (not a typing.Protocol) on purpose: we own every implementation,
we want construction-time enforcement of the method set, and we share the
async-context-manager helper.
"""

from __future__ import annotations

import abc

from ..models import ExecResult


class SandboxError(RuntimeError):
    """An infra-level sandbox failure: the daemon/hypervisor failed, the sandbox
    could not start, or a control operation failed -- the WHOLE sandbox is dead.

    Distinct from a command exiting nonzero (a normal ExecResult). The caller maps
    SandboxError to an `infra_failed` trial -- retryable, NOT counted against the
    model. A nonzero exit code is a model result and is NEVER an error.
    """


class TerminalError(RuntimeError):
    """A single terminal is unusable (unknown id, or wedged past recovery). Unlike
    SandboxError (whole sandbox dead = infra), this is the agent's concern and is
    RECOVERABLE -- it can open a new terminal and continue. The ToolServer turns it
    into an observation, never a crash.
    """


class Environment(abc.ABC):
    """One disposable sandbox. Implementations: DockerEnvironment, etc.

    Concrete subclasses are constructed with the slice of a resolved `Task` they
    need (`image_ref`, `resources`, `network`, `workdir`) -- they do not take a
    raw task_id and do not depend on the Registry.

    Lifecycle: start() -> (exec / open_shell / read / write)* -> snapshot()? -> destroy().
    Use as an async context manager to guarantee teardown.
    """

    # --- lifecycle -------------------------------------------------------

    @abc.abstractmethod
    async def start(self) -> None:
        """Boot the sandbox from the (already-built) image. Egress off by default.
        Raises SandboxError if the sandbox cannot be created.
        """

    @abc.abstractmethod
    async def destroy(self) -> None:
        """Tear the sandbox down and release all resources (incl. open terminals).
        Idempotent; safe after a failed start or twice; never raises on a missing
        sandbox -- cleanup must always succeed.
        """

    # --- command execution ----------------------------------------------

    @abc.abstractmethod
    async def exec(
        self,
        cmd: str,
        *,
        terminal_id: str | None = None,
        cwd: str = "/workspace",
        timeout_s: int | None = None,
        env: dict[str, str] | None = None,
    ) -> ExecResult:
        """Run a shell command and return its ExecResult.

        terminal_id=None  -> one-shot in a fresh shell. `cwd`/`env` apply. Timeout
            kills cleanly (ExecResult(timed_out=True, exit_code=124)).
        terminal_id=<id>  -> runs in that persistent terminal (from open_shell()).
            Session state (cwd, env vars, venv) persists; `cwd`/`env` args are
            ignored (manage them in-band with `cd`/`export`). On timeout the running
            command is killed to unwedge the terminal; timed_out=True is returned.

        timeout_s: per-command wall-clock cap. None -> the environment's configured
            default. Execution is ALWAYS bounded -- there is no unbounded mode.

        Returns an ExecResult for any command that ran, including a nonzero exit.
        Raises SandboxError only on an infra failure (sandbox/terminal gone).
        """

    @abc.abstractmethod
    async def open_shell(self) -> str:
        """Start a new persistent terminal (a long-lived shell process inside the
        sandbox) and return its opaque terminal_id.
        """

    @abc.abstractmethod
    async def close_shell(self, terminal_id: str) -> None:
        """Close a persistent terminal. Idempotent; safe if already gone."""

    # --- filesystem ------------------------------------------------------
    # System-level fs ops (verifier reads reward.json; infra injects tests/ and
    # applies the solution). Binary-safe; independent of any terminal's state.

    @abc.abstractmethod
    async def read_file(self, path: str, *, max_bytes: int = 10_000) -> bytes:
        """Read up to `max_bytes` from `path` in the sandbox. Binary-safe."""

    @abc.abstractmethod
    async def write_file(self, path: str, content: bytes) -> None:
        """Write `content` to `path` in the sandbox, creating parent dirs.
        Binary-safe (no shell escaping of the payload; no arg-length limit).
        """

    # --- snapshot --------------------------------------------------------

    @abc.abstractmethod
    async def snapshot(self) -> str:
        """Capture the final state and return an opaque snapshot ref. The env only
        PRODUCES the snapshot; persisting it (to an ArtifactStore) is the caller's
        job -- the sandbox is not coupled to any storage backend.
        """

    # --- async context manager (shared, concrete) ------------------------

    async def __aenter__(self) -> "Environment":
        await self.start()
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self.destroy()
