import json
import sys

from storage import EventStore


def process(events_path: str, db_path: str) -> None:
    """Read events from a JSONL file (one event per line) and persist each."""
    store = EventStore(db_path)
    with open(events_path) as f:
        data = json.load(f)  # parse the input
    for event in data:
        store.persist(event["id"], json.dumps(event["payload"]))


if __name__ == "__main__":
    process(sys.argv[1], sys.argv[2])
