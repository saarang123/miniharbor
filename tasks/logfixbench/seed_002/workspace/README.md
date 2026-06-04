# Event worker

A minimal event-ingestion service for an at-least-once delivery stream.

- `storage.py` — `EventStore`, a SQLite-backed store.
- `worker.py` — `process(events_path, db_path)` reads events from a JSONL file
  (one `{"id": ..., "payload": ...}` per line) and persists each via the store.
- `tests_public.py` — a public smoke test you can run.

Run the public tests:

```
python -m pytest tests_public.py -q
```
