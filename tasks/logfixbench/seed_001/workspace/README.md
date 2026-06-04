# Event worker

A minimal batch event-processing service.

- `storage.py` — `EventStore`, an idempotent SQLite-backed store keyed by `event_id`.
- `worker.py` — `process(events_path, db_path)` reads events from a JSONL file
  (one `{"id": ..., "payload": ...}` per line) and persists each via the store.
- `tests_public.py` — a public smoke test you can run.

Run the public tests:

```
python -m pytest tests_public.py -q
```
