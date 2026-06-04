# Orchestrator

Expands a job into trials, queues them, schedules them onto workers, and collects results. At v1 this is a concurrency cap wearing a `Scheduler` interface; the interface is shaped so the production swap (bin-packing / cell-based scheduling) is a drop-in.

> Ports: `Scheduler`, `Store`, `ArtifactStore`. v1 adapters: FIFO claim with a concurrency cap; SQLite; local filesystem. Swap-to: Redis/Kafka queue + bin-packing scheduler; Postgres; object store.

## Responsibilities

```
Job  ──expand──►  N×M TrialSpecs  ──enqueue──►  Scheduler  ──claim──►  Worker runs trial  ──►  Store + ArtifactStore
```

1. **Expand.** A `Job` (`agent_cfg`, `model`, `task_set` of M tasks, `attempts` = N, budgets, `harness_version`) becomes `N × M` independent `TrialSpec`s. Each `trial_id` is deterministic from `(job_id, task_name, attempt)` for idempotency.
2. **Enqueue.** Trials are written to the store as `queued` and placed on the queue.
3. **Schedule.** Workers claim trials subject to capacity.
4. **Run.** A worker executes the trial (boot env → harness → verifier → snapshot → log) and writes the `TrialResult`.
5. **Collect.** Aggregate per-job metrics from trial results.

## Interfaces

```python
class WorkerCtx(BaseModel):
    worker_id: str
    free_cpu: int
    free_memory_gb: int

class Scheduler(Protocol):
    async def submit(self, trials: list[TrialSpec]) -> None: ...
    async def claim(self, worker: WorkerCtx) -> TrialSpec | None: ...   # None when nothing fits
    async def complete(self, trial_id: str, result: TrialResult) -> None: ...
    async def requeue(self, trial_id: str) -> None: ...                 # for infra_failed retries

class Store(Protocol):
    async def create_job(self, job: Job) -> None: ...
    async def upsert_trial(self, spec: TrialSpec, status: TrialStatus) -> None: ...
    async def record_result(self, result: TrialResult) -> None: ...
    async def job_metrics(self, job_id: str) -> JobMetrics: ...

class ArtifactStore(Protocol):
    async def put(self, key: str, data: bytes) -> str: ...   # returns ref
    async def get(self, key: str) -> bytes: ...
```

## v1 scheduler — FIFO with a concurrency cap

Bin-packing is trivial at small scale: a single concurrency limit (`--n-concurrent`). The scheduler hands out the next `queued` trial whenever a worker slot is free; resource fields exist on `TrialSpec`/`WorkerCtx` but the v1 policy only checks "is a slot free."

```python
class FifoScheduler:
    def __init__(self, store, max_concurrent: int):
        self.store, self.sem = store, asyncio.Semaphore(max_concurrent)
    async def claim(self, worker):
        async with self.sem:
            return await self.store.next_queued()      # SELECT ... FOR UPDATE SKIP LOCKED
```

The store-as-queue (`SKIP LOCKED`) gives at-least-once claim semantics and safe concurrent workers without a separate broker. Fan-out within one worker uses `asyncio.TaskGroup`.

## Swap-to — bin-packing / cell-based

The production scheduler uses the resource fields it ignored at v1: pack trials onto hosts by `(cpu, memory, disk)` so a host runs as many trials as fit, and isolate tenants/jobs into cells so one runaway job cannot starve another. The queue moves to Redis/Kafka for durability and per-tenant partitioning. The `Scheduler` interface is unchanged — `claim` simply returns the best-fitting trial for the worker's free resources instead of the next FIFO one.

| Concern | v1 | Swap-to |
|---|---|---|
| Queue | store table + `SKIP LOCKED` | Redis Streams / Kafka (partitioned) |
| Placement | concurrency cap | bin-pack by resources; cell isolation |
| Workers | one process, `asyncio.TaskGroup` | a worker fleet (abstract worker role) |
| Autoscale signal | none | queue depth / consumer lag |

## Retries and idempotency

- `infra_failed` trials are **retryable**: `requeue` puts them back; the deterministic `trial_id` plus `ON CONFLICT DO NOTHING` makes re-running safe (no double-counting).
- Valid model results (`passed`, `failed_tests`, `agent_failed`, `timed_out`) are never retried.
- `verifier_failed` halts and flags the task version; it is not a model result and not a simple retry.
- Claims use a lease (a visibility timeout ≥ the trial wall-clock budget): if a worker dies mid-trial, the lease expires and the trial is reclaimable.

## Collection and metrics

After trials complete, `job_metrics` derives the report from trial results: pass@1, pass@k, timeout rate, average steps, average runtime, infra-failure count (which should be excluded from the model's pass-rate). These come from the metadata store; trajectory-level detail comes from the artifact store on demand.
