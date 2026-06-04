# Fix duplicate events under at-least-once delivery

The service in this workspace ingests events from a JSONL stream and must
persist **every event exactly once**. The upstream uses at-least-once delivery,
so the input sometimes contains the same event (same `id`) more than once.
Right now duplicates are being stored multiple times — the persisted count is
higher than the number of distinct events.

Your task:

1. Inspect `worker.py` and `storage.py`.
2. Fix it so that each distinct `event_id` is persisted exactly once, even when
   the input delivers it several times or the worker is run more than once.
3. Do not change the public API of `EventStore` (`persist`, `count`, `ids`).

You can reproduce the issue and check your work with:

```
python -m pytest tests_public.py -q
```

When you are confident the fix is correct, submit.
