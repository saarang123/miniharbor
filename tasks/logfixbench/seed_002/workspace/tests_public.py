import json
import os

from storage import EventStore
import worker


def test_dedupes_small(tmp_path):
    events = [
        {"id": "a", "payload": {}},
        {"id": "b", "payload": {}},
        {"id": "a", "payload": {}},  # duplicate delivery
    ]
    events_path = os.path.join(str(tmp_path), "events.jsonl")
    db_path = os.path.join(str(tmp_path), "events.db")
    with open(events_path, "w") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")

    worker.process(events_path, db_path)

    # Two distinct ids -> two stored rows, despite "a" arriving twice.
    assert EventStore(db_path).count() == 2
