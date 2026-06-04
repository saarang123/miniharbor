# Environment

The sandbox. Takes an image definition, sets up an isolated execution context, and exposes a fixed six-method interface for running commands, moving files, snapshotting, and teardown. The same interface is implemented by Docker, a microVM, and hosted providers — only the implementation of the methods changes.

> Port. v1 adapter: Docker. Swap-to: Firecracker microVM, Kata, gVisor, hosted (E2B/Modal).

## Interface

```python
class ExecResult(BaseModel):
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool
    duration_ms: int

class Environment(Protocol):
    async def start(self) -> None: ...
    async def exec(self, cmd: str, cwd: str = "/workspace",
                   timeout_s: int = 30, env: dict[str, str] | None = None) -> ExecResult: ...
    async def read_file(self, path: str, max_bytes: int = 10_000) -> bytes: ...
    async def write_file(self, path: str, content: bytes) -> None: ...
    async def start_process(self, cmd: str, cwd: str = "/workspace") -> str: ...
    async def read_process_output(self, process_id: str, max_bytes: int = 10_000) -> str: ...
    async def stop_process(self, process_id: str) -> None: ...
    async def snapshot(self) -> str: ...
    async def destroy(self) -> None: ...
```

The interface is deliberately exactly this wide: `exec` + filesystem + long-running process control + snapshot + teardown. Everything an agent or verifier needs is one of these. The ToolServer ([`tool-server.md`](tool-server.md)) is the only caller of `exec`/fs/process; the harness calls `start`/`snapshot`/`destroy`; the verifier calls `exec`.

## Why this interface is the whole point

A task ships an image definition plus test files. Neither is bound to any particular runtime — both need only (a) a filesystem holding the task's initial state and (b) a way to run commands in it. `docker exec` provides that; so does a `vsock → guest-agent` transport into a microVM. So the six methods are implementable by every backend, and the rest of the system is backend-agnostic.

The verifier ports for free: it is files run through the same `exec` path, writing a reward to a known file, regardless of backend.

## Image porting — how an image definition reaches each backend

The portable artifact is an **OCI image**: filesystem layers plus a config (entrypoint, env, cwd). "Run it in backend X" means: get that rootfs into a place X can use, then exec inside.

### Docker adapter (v1)

```
build:   docker build environment/  → image (record content digest as image_ref)
start:   docker run -d --network none --cpus N --memory Mg <image_ref> sleep infinity
exec:    docker exec <cid> bash -lc "<cmd>"          (with cwd, timeout via timeout(1), env)
files:   docker cp / exec cat / exec tee
process: exec a backgrounded command tracked by pid; read its log file
snapshot: docker commit / docker export the overlay diff → artifact
destroy: docker rm -f <cid>
egress:  --network none
```

Trusted-author tasks in v1, so the image is built locally on the shared daemon. The build step is its own seam (`BuildService`): the production swap is an isolated builder (rootless Kaniko/BuildKit or a throwaway VM) so an untrusted image definition cannot escape during build.

### Firecracker microVM adapter (the building-blocks target)

Firecracker is not OCI-aware; the port is done by hand, which is exactly the point — you own every step.

```
1. flatten layers → rootfs.ext4
     docker create <image_ref> → docker export → tar of merged rootfs
     mkfs.ext4 a file; mount; untar the rootfs in
     (or use firecracker-containerd's snapshotter to build the block device from layers)
2. supply a guest kernel (vmlinux), built once, image-agnostic
3. inject a guest agent into the rootfs that boots as init, listens on vsock,
   and exposes exec / read / write / spawn  ← this IS the sandbox side of the ToolServer
4. boot via the Firecracker API:
     PUT /boot-source   { kernel_image_path, boot_args: "... root=/dev/vda" }
     PUT /drives/rootfs { path_on_host: rootfs.ext4, is_root_device: true }
     PUT /machine-config{ vcpu_count, mem_size_mib }
     (attach NO network interface → egress off)
     add a vsock device
     PUT /actions       { action_type: "InstanceStart" }
5. exec:    host ToolServer → vsock RPC → guest agent runs cmd → returns ExecResult
   snapshot: Firecracker pause + native memory+disk snapshot (restore via on-demand paging)
   destroy:  kill the firecracker process
```

For an eval harness you do **not** need full OCI container semantics inside the VM. The agent drives everything via `exec`; you do not have to honor the image `ENTRYPOINT`. You port the *filesystem* (task state + installed deps), not the entire container runtime contract. That is what makes the hand-rolled path tractable.

### Kata adapter (automatic OCI port)

Kata is OCI-compatible: point containerd at the `kata` runtime instead of `runc` and the image runs unchanged inside a microVM. Kata materializes the rootfs, boots the VM (QEMU / Cloud Hypervisor / Firecracker / Dragonball as the hypervisor), and the in-guest kata-agent (ttRPC over vsock) handles `exec` and lifecycle. This is the production "automatic porting" answer — you do not write the porting, the runtime does. Use it when contractor-supplied images must run without hand-rolling.

### gVisor adapter (optional middle rung)

`runsc` is an OCI runtime drop-in: real syscall boundary (user-space Sentry) without a separate guest kernel, automatic image port like Kata. Lower effort than a microVM, but not a microVM (no guest kernel). File as an optional adapter, not the main target.

### Hosted adapters (later)

E2B / Modal expose an SDK that already implements `exec`/files; the adapter maps the six methods onto their client. Useful to offload the sandbox fleet entirely.

## Backend differences (everything else is identical)

| Op | Docker | Firecracker | Kata |
|---|---|---|---|
| port/build | `docker build` | flatten layers → ext4 + kernel + guest agent | OCI image as-is |
| start | `docker run --network none` | Firecracker API; no tap; vsock; InstanceStart | `ctr run --runtime kata` |
| exec | `docker exec` | vsock → guest agent | ttRPC → kata-agent over vsock |
| egress off | `--network none` | attach no network device | runtime config |
| snapshot | overlay diff export | native memory+disk snapshot | hypervisor snapshot |
| destroy | `docker rm -f` | kill firecracker process | shim teardown |
| host-kernel reachable | yes | no | no |

## Resource limits and egress

- CPU / memory / disk / pids come from `task.toml [resources]`. Docker: `--cpus`, `--memory`, `--pids-limit`, `--storage-opt`. microVM: vCPU count + `mem_size_mib` + ext4 size; the VMM enforces the rest.
- Egress is **off by default** for every adapter. Any allowlist (e.g. to reach a model endpoint when the agent runs inside the sandbox) is an explicit, named exception, not the default.

## Snapshot and rehydration

`snapshot()` persists the final state to the artifact store keyed by `trial_id`. You do not keep sandboxes alive; you store cheap state and rehydrate on demand into a fresh, locked-down sandbox for inspection. Docker stores a base-image-digest + overlay diff; a microVM stores a memory+disk snapshot whose restore uses on-demand paging. Rehydration boots a throwaway environment from the snapshot with egress off and a short TTL.

## v1 build order

1. `DockerEnvironment` — full six methods, `--network none`, overlay-diff snapshot. Closes the loop.
2. `FirecrackerEnvironment` for one task — hand-roll steps 1–5 above, including the vsock guest agent. This is the high-value lesson.
3. Note Kata/gVisor/hosted as adapters to add behind the same interface when needed.
