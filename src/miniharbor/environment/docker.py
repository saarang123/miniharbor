"""Docker adapter for the Environment interface.

One container per trial, driven through the `docker` CLI (not the SDK): every
action is a command you could type, and there is no extra dependency. Each method
shells out via asyncio.create_subprocess_exec so many trials run concurrently
without blocking the event loop.

`build` is deliberately a module function, NOT an Environment method -- building
is the divergence point between backends (Docker builds an image; a microVM
flattens layers into a rootfs), so it stays outside the portable interface.
"""

from __future__ import annotations

import asyncio
import os
import shlex
import tempfile
import time
import uuid

from ..models import Task
from ..models import ExecResult
from .base import Environment, SandboxError


async def _run(*argv: str, input_bytes: bytes | None = None, timeout: float | None = None):
    """Run a HOST command (argv list, NO shell -> no host-side injection).
    Returns (returncode, stdout_bytes, stderr_bytes).
    """
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE if input_bytes is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
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


class DockerEnvironment(Environment):
    def __init__(self, task: Task, *, name: str | None = None):
        # Only the slice the sandbox needs -- never the whole task's grading info.
        self._image = task.image_ref
        self._resources = task.resources
        self._network = task.network
        self._workdir = task.workdir
        self._name = name or f"mh-{uuid.uuid4().hex[:12]}"   # unique => parallel-safe
        self._cid: str | None = None
        self._procs: dict[str, tuple[str, str]] = {}          # proc_id -> (pid, logfile)

    # --- lifecycle ------------------------------------------------------

    async def start(self) -> None:
        r = self._resources
        # CMD is `sleep infinity`: the container is a long-lived shell HOST we exec
        # into, not a one-shot `docker run cmd`. That is what persists filesystem
        # state across the agent's steps.
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
        # Always succeeds, idempotent -- otherwise containers leak (the 50th-trial bug).
        if self._cid is None:
            return
        await _run("docker", "rm", "-f", self._cid)
        self._cid = None

    # --- exec -----------------------------------------------------------

    async def exec(self, cmd, *, cwd="/workspace", timeout_s=30, env=None) -> ExecResult:
        cid = self._require_started()
        argv = ["docker", "exec", "--workdir", cwd]
        for k, v in (env or {}).items():
            argv += ["--env", f"{k}={v}"]
        # Two-layer timeout. Inner `timeout` kills the command cleanly inside the
        # container and exits 124 on a trip. The wrapped string is ONE argv element
        # to bash -lc, so the host shell never sees `cmd` -- only the in-container
        # shell runs it.
        wrapped = (
            f"timeout --signal=TERM --kill-after=5s {timeout_s}s "
            f"bash -lc {shlex.quote(cmd)}"
        )
        argv += [cid, "bash", "-lc", wrapped]

        start = time.monotonic()
        try:
            # Outer backstop: if `docker exec` itself hangs (wedged daemon), don't
            # block the event loop forever. Generous margin over the inner timeout.
            rc, out, err = await _run(*argv, timeout=timeout_s + 15)
        except asyncio.TimeoutError:
            return ExecResult(
                stdout="",
                stderr="docker exec did not return (sandbox may be wedged)",
                exit_code=124, timed_out=True,
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        dur = int((time.monotonic() - start) * 1000)
        # Distinguish a daemon-level failure (the container is gone / can't exec)
        # from a command that merely exited nonzero. The former is infra; the latter
        # is a normal model result and is NEVER an error.
        if self._looks_like_daemon_error(err):
            raise SandboxError(f"docker exec failed: {err.decode(errors='replace')}")
        return ExecResult(
            stdout=out.decode(errors="replace"),
            stderr=err.decode(errors="replace"),
            exit_code=rc,
            timed_out=(rc == 124),                            # coreutils timeout exit code
            duration_ms=dur,
        )

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
        await self.exec(f"mkdir -p {shlex.quote(os.path.dirname(path) or '/')}")
        fd, tmp = tempfile.mkstemp()
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(content)
            rc, _out, err = await _run("docker", "cp", tmp, f"{cid}:{path}")
            if rc != 0:
                raise SandboxError(f"write_file({path}) failed: {err.decode(errors='replace')}")
        finally:
            os.unlink(tmp)

    # --- long-running processes -----------------------------------------

    async def start_process(self, cmd, *, cwd="/workspace") -> str:
        self._require_started()
        proc_id = f"proc_{uuid.uuid4().hex[:8]}"
        log = f"/tmp/{proc_id}.log"
        # setsid => the child is its own session/group leader, survives the exec
        # session that launched it, and can be killed as a group. `echo $!` returns
        # its PID. FRAGILE BIT: the child is reparented to the container's init; a
        # robust version would use a real supervisor. Good enough for v1.
        launch = (
            f"cd {shlex.quote(cwd)} && "
            f"setsid bash -lc {shlex.quote(cmd)} >{log} 2>&1 </dev/null & echo $!"
        )
        res = await self.exec(launch)
        self._procs[proc_id] = (res.stdout.strip(), log)
        return proc_id

    async def read_process_output(self, process_id, *, max_bytes=10_000) -> str:
        _pid, log = self._procs[process_id]
        res = await self.exec(f"tail -c {max_bytes} {shlex.quote(log)} 2>/dev/null || true")
        return res.stdout

    async def stop_process(self, process_id) -> None:
        pid, _log = self._procs.get(process_id, ("", ""))
        if pid:
            # kill the whole process group (setsid made `pid` the group leader)
            await self.exec(f"kill -- -{pid} 2>/dev/null || kill {pid} 2>/dev/null || true")

    # --- snapshot -------------------------------------------------------

    async def snapshot(self) -> str:
        cid = self._require_started()
        ref = f"{self._name}-snap"
        # commit captures the filesystem (not process memory) -- enough for v1
        # inspection. A microVM backend would use a native memory+disk snapshot.
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
