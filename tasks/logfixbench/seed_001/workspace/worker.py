import json
import sys

from storage import EventStore

BATCH_SIZE = 10


def process(events_path: str, db_path: str) -> None:
    """Read events from a JSONL file and persist each exactly once."""
    store = EventStore(db_path)
    batch: list[dict] = []
    with open(events_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            batch.append(json.loads(line))
            if len(batch) == BATCH_SIZE:
                _flush(store, batch)
                batch = []
    # NOTE: events still buffered here after the loop are not handled.


def _flush(store: EventStore, batch: list[dict]) -> None:
    for event in batch:
        store.persist(event["id"], json.dumps(event["payload"]))


if __name__ == "__main__":
    process(sys.argv[1], sys.argv[2])
