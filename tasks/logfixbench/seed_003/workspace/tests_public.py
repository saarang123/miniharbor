import json
import os

from storage import EventStore
import worker


def test_reads_multiline_jsonl(tmp_path):
    events_path = os.path.join(str(tmp_path), "events.jsonl")
    db_path = os.path.join(str(tmp_path), "events.db")
    with open(events_path, "w") as f:
        for i in range(5):
            f.write(json.dumps({"id": f"e{i}", "payload": {"n": i}}) + "\n")

    worker.process(events_path, db_path)

    # JSONL with 5 lines: json.load() chokes on multi-line input.
    assert EventStore(db_path).count() == 5
