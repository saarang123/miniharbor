import json
import sys

from storage import EventStore


def process(events_path: str, db_path: str) -> None:
    """Persist each event exactly once, even if the input delivers it twice."""
    store = EventStore(db_path)
    with open(events_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            event = json.loads(line)
            store.persist(event["id"], json.dumps(event["payload"]))


if __name__ == "__main__":
    process(sys.argv[1], sys.argv[2])
