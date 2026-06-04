# Fix dropped events in the batch worker

The service in this workspace ingests events from a JSONL file and is supposed
to persist **every event exactly once**. In production, some events are being
silently dropped — the persisted count is lower than the number of input events
for certain input sizes.

Your task:

1. Inspect `worker.py` and `storage.py`.
2. Find the cause of the dropped events and fix it so that every event in the
   input is persisted exactly once, for any input size.
3. Do not change the public API of `EventStore` (`persist`, `count`, `ids`).

You can reproduce the issue and check your work with:

```
python -m pytest tests_public.py -q
```

When you are confident the fix is correct, submit.
