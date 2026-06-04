# Fix the ingest worker that persists nothing

The service in this workspace ingests events and persists each exactly once. The
input is a JSONL stream — one JSON event per line. Right now the worker fails to
ingest multi-line input: no events (or the wrong number) get persisted.

Your task:

1. Inspect `worker.py` and `storage.py`.
2. Fix the worker so it correctly reads the JSONL input (one event per line) and
   persists every event exactly once.
3. Do not change the public API of `EventStore` (`persist`, `count`, `ids`).

You can reproduce the issue and check your work with:

```
python -m pytest tests_public.py -q
```

When you are confident the fix is correct, submit.
