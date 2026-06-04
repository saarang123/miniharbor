import json
import os

from storage import EventStore
import worker


def test_all_persisted_small(tmp_path):
    events_path = os.path.join(str(tmp_path), "events.jsonl")
    db_path = os.path.join(str(tmp_path), "events.db")
    with open(events_path, "w") as f:
        for i in range(25):
            f.write(json.dumps({"id": f"e{i}", "payload": {"n": i}}) + "\n")

    worker.process(events_path, db_path)

    # 25 events with BATCH_SIZE=10: the last partial batch is dropped by the bug.
    assert EventStore(db_path).count() == 25
