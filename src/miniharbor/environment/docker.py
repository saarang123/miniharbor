"""Docker adapter for the Environment interface.

One container per trial via the `docker` CLI (transparent, dependency-free),
async throughout. Two execution modes:

  * one-shot  (terminal_id=None): a fresh `docker exec` + coreutils `timeout`
    (clean kill, exit 124). Used by the verifier and trial setup.
  * terminal  (terminal_id=<id>): a long-lived `docker exec -i <cid> bash -l`
    process whose stdin we hold open. cwd/env/venv persist. Command completion
    and exit code are detected with a per-command sentinel (no tmux, no PTY).

`build` is a module function, NOT an env method (the Docker<->microVM divergence
point stays outside the portable interface).
"""

from __future__ import annotations

import asyncio
import os
import shlex
import tempfile
import time
import uuid

from ..models import ExecResult, Task
from .base import Environment, SandboxError

_PIPE = asyncio.subprocess.PIPE
_STDOUT = asyncio.subprocess.STDOUT

# Per-command default cap when the caller passes timeout_s=None. Execution is
# always bounded; this is just the fallback when neither agent nor task specifies.
DEFAULT_TIMEOUT_S = 300


async def _run(*argv: str, input_bytes: bytes | None = None, timeout: float | None = None):
    """Run a HOST command (argv, NO shell). Returns (returncode, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdin=_PIPE if input_bytes is not None else None,
        stdout=_PIPE,
        stderr=_PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(input=input_bytes), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise
    return proc.returncode, out, err


async def build_image(bundle_path: str, tag: str | None = None) -> str:
    """Build a task bundle's image. Context = bundle root, -f environment/Dockerfile.
    Returns the image ref. The BuildService seam (trusted-author build in v1).
    """
    bundle_path = os.path.abspath(bundle_path)
    name = os.path.basename(bundle_path.rstrip("/"))
    tag = tag or f"miniharbor/{name}:latest"
    dockerfile = os.path.join(bundle_path, "environment", "Dockerfile")
    rc, _out, err = await _run("docker", "build", "-f", dockerfile, "-t", tag, bundle_path)
    if rc != 0:
        raise SandboxError(f"image build failed for {name}:\n{err.decode(errors='replace')}")
    return tag


async def _drain_until(reader: asyncio.StreamReader, marker: str) -> tuple[str, int]:
    """Read lines until one contains `marker`. Return (text_before_marker, int_after).
    Raises SandboxError if the stream hits EOF first (the shell died).
    """
    buf: list[str] = []
    while True:
        line = await reader.readline()
        if not line:
            raise SandboxError("terminal stream closed unexpectedly")
        text = line.decode(errors="replace")
        i = text.find(marker)
        if i != -1:
            if text[:i]:
                buf.append(text[:i])
            tail = text[i + len(marker):].strip()
            try:
                code = int(tail)
            except ValueError:
                code = 0
            return "".join(buf), code
        buf.append(text)


class _Shell:
    """A held-open `docker exec -i bash` process = one persistent terminal."""

    def __init__(self, proc: asyncio.subprocess.Process):
        self.proc = proc
        self.pid: int | None = None          # the bash PID *inside* the container
        self.lock = asyncio.Lock()           # serialize commands on this terminal
        self.broken = False


class DockerEnvironment(Environment):
    def __init__(self, task: Task, *, name: str | None = None, default_timeout_s: int | None = None):
        # Only the slice the sandbox needs -- never the task's grading info.
        self._image = task.image_ref
        self._resources = task.resources
        self._network = task.network
        self._workdir = task.workdir
        # Per-command default cap (task/worker-configurable); used when exec is
        # called with timeout_s=None. Always bounded.
        self._default_timeout_s = default_timeout_s or DEFAULT_TIMEOUT_S
        self._name = name or f"mh-{uuid.uuid4().hex[:12]}"   # unique => parallel-safe
        self._cid: str | None = None
        self._shells: dict[str, _Shell] = {}

    # --- lifecycle ------------------------------------------------------

    async def start(self) -> None:
        r = self._resources
        # CMD is `sleep infinity`: a long-lived shell HOST we exec into.
        rc, out, err = await _run(
            "docker", "run", "-d",
            "--name", self._name,
            f"--network={self._network}",                     # 'none' => egress off
            f"--cpus={r.cpu}",
            f"--memory={int(r.memory_gb * 1024)}m",
            f"--pids-limit={r.pids}",
            "--workdir", self._workdir,
            self._image,
        )
        if rc != 0:
            raise SandboxError(f"container start failed:\n{err.decode(errors='replace')}")
        self._cid = out.decode().strip()

    async def destroy(self) -> None:
        # Always succeeds, idempotent. Close terminals first, then remove container.
        for tid in list(self._shells):
            await self.close_shell(tid)
        if self._cid is not None:
            await _run("docker", "rm", "-f", self._cid)
            self._cid = None

    # --- command execution ----------------------------------------------

    async def exec(self, cmd, *, terminal_id=None, cwd="/workspace", timeout_s=None, env=None) -> ExecResult:
        self._require_started()
        t = timeout_s if timeout_s is not None else self._default_timeout_s   # always bounded
        if terminal_id is None:
            return await self._exec_oneshot(cmd, cwd=cwd, timeout_s=t, env=env)
        return await self._exec_in_terminal(terminal_id, cmd, timeout_s=t)

    async def _exec_oneshot(self, cmd, *, cwd, timeout_s, env) -> ExecResult:
        cid = self._require_started()
        argv = ["docker", "exec", "--workdir", cwd]
        for k, v in (env or {}).items():
            argv += ["--env", f"{k}={v}"]
        # Inner coreutils `timeout` kills cleanly (exit 124); the wrapped string is
        # ONE argv element to bash -lc, so the host shell never sees `cmd`.
        wrapped = f"timeout --signal=TERM --kill-after=5s {timeout_s}s bash -lc {shlex.quote(cmd)}"
        argv += [cid, "bash", "-lc", wrapped]

        start = time.monotonic()
        try:
            rc, out, err = await _run(*argv, timeout=timeout_s + 15)   # outer backstop
        except asyncio.TimeoutError:
            return ExecResult(
                stdout="", stderr="docker exec did not return (sandbox may be wedged)",
                exit_code=124, timed_out=True,
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        dur = int((time.monotonic() - start) * 1000)
        if self._looks_like_daemon_error(err):
            raise SandboxError(f"docker exec failed: {err.decode(errors='replace')}")
        return ExecResult(
            stdout=out.decode(errors="replace"),
            stderr=err.decode(errors="replace"),
            exit_code=rc,
            timed_out=(rc == 124),
            duration_ms=dur,
        )

    async def _exec_in_terminal(self, terminal_id, cmd, *, timeout_s) -> ExecResult:
        shell = self._shells.get(terminal_id)
        if shell is None or shell.broken:
            raise SandboxError(f"no live terminal {terminal_id!r}")
        # One terminal = one command at a time. The persistent shell merges
        # stdout+stderr (like a real terminal), so ExecResult.stdout is combined.
        async with shell.lock:
            nonce = uuid.uuid4().hex[:8]
            marker = f"__MH_DONE_{nonce}__"
            # Send the command, then a sentinel printing the command's exit code.
            payload = f"{cmd}\nprintf '\\n{marker}%d\\n' \"$?\"\n"
            shell.proc.stdin.write(payload.encode())
            await shell.proc.stdin.drain()

            start = time.monotonic()
            try:
                out, code = await asyncio.wait_for(_drain_until(shell.proc.stdout, marker), timeout=timeout_s)
                timed_out = False
            except asyncio.TimeoutError:
                timed_out = True
                # Unwedge: kill the running command (children of the shell) via a
                # one-shot exec; the queued sentinel then fires and we drain it.
                if shell.pid is not None:
                    await self._exec_oneshot(
                        f"pkill -TERM -P {shell.pid} 2>/dev/null || true",
                        cwd="/", timeout_s=10, env=None,
                    )
                try:
                    out, _ = await asyncio.wait_for(_drain_until(shell.proc.stdout, marker), timeout=5)
                except asyncio.TimeoutError:
                    out = ""
                    shell.broken = True       # could not recover; terminal is unusable
                code = 124
            return ExecResult(
                stdout=out, stderr="", exit_code=code, timed_out=timed_out,
                duration_ms=int((time.monotonic() - start) * 1000),
            )

    async def open_shell(self) -> str:
        cid = self._require_started()
        # `bash -l` (login, non-interactive over a pipe): sources profile like the
        # one-shot `bash -lc`, prints no prompt. stderr merged into stdout.
        proc = await asyncio.create_subprocess_exec(
            "docker", "exec", "-i", cid, "bash", "-l",
            stdin=_PIPE, stdout=_PIPE, stderr=_STDOUT,
        )
        shell = _Shell(proc)
        # Capture the in-container bash PID for kill-on-timeout recovery. Any
        # profile banner noise is absorbed as the (discarded) text before the marker.
        proc.stdin.write(b"echo __MH_PID__$$\n")
        await proc.stdin.drain()
        try:
            _pre, pid = await asyncio.wait_for(_drain_until(proc.stdout, "__MH_PID__"), timeout=10)
        except (asyncio.TimeoutError, SandboxError) as exc:
            proc.kill()
            raise SandboxError(f"could not open terminal: {exc}")
        shell.pid = pid
        tid = f"term_{uuid.uuid4().hex[:8]}"
        self._shells[tid] = shell
        return tid

    async def close_shell(self, terminal_id) -> None:
        shell = self._shells.pop(terminal_id, None)
        if shell is None:
            return
        try:
            shell.proc.stdin.write(b"exit\n")
            await shell.proc.stdin.drain()
            await asyncio.wait_for(shell.proc.wait(), timeout=5)
        except Exception:
            try:
                shell.proc.kill()
            except ProcessLookupError:
                pass

    # --- filesystem (docker cp: binary-safe, no escaping, no arg-length limit) ---

    async def read_file(self, path, *, max_bytes=10_000) -> bytes:
        cid = self._require_started()
        fd, tmp = tempfile.mkstemp()
        os.close(fd)
        try:
            rc, _out, err = await _run("docker", "cp", f"{cid}:{path}", tmp)
            if rc != 0:
                raise SandboxError(f"read_file({path}) failed: {err.decode(errors='replace')}")
            with open(tmp, "rb") as fh:
                return fh.read(max_bytes)
        finally:
            os.unlink(tmp)

    async def write_file(self, path, content) -> None:
        cid = self._require_started()
        await self._exec_oneshot(
            f"mkdir -p {shlex.quote(os.path.dirname(path) or '/')}",
            cwd="/", timeout_s=30, env=None,
        )
        fd, tmp = tempfile.mkstemp()
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(content)
            rc, _out, err = await _run("docker", "cp", tmp, f"{cid}:{path}")
            if rc != 0:
                raise SandboxError(f"write_file({path}) failed: {err.decode(errors='replace')}")
        finally:
            os.unlink(tmp)

    # --- snapshot -------------------------------------------------------

    async def snapshot(self) -> str:
        cid = self._require_started()
        ref = f"{self._name}-snap"
        rc, out, err = await _run("docker", "commit", cid, ref)
        if rc != 0:
            raise SandboxError(f"snapshot failed: {err.decode(errors='replace')}")
        return out.decode().strip() or ref

    # --- helpers --------------------------------------------------------

    def _require_started(self) -> str:
        if self._cid is None:
            raise SandboxError("environment not started (call start() first)")
        return self._cid

    @staticmethod
    def _looks_like_daemon_error(err: bytes) -> bool:
        s = err.decode(errors="replace")
        return (
            "Error response from daemon" in s
            or "No such container" in s
            or "is not running" in s
        )
