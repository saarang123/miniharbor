import json
import os
import sys

import pytest

# The agent's (possibly modified) code lives at /workspace.
sys.path.insert(0, "/workspace")

import worker  # noqa: E402
from storage import EventStore  # noqa: E402


def _run(n, tmp):
    events_path = os.path.join(tmp, "events.jsonl")
    db_path = os.path.join(tmp, "events.db")
    with open(events_path, "w") as f:
        for i in range(n):
            f.write(json.dumps({"id": f"e{i}", "payload": {"n": i}}) + "\n")
    worker.process(events_path, db_path)
    return EventStore(db_path)


@pytest.mark.parametrize("n", [1, 2, 25, 100])
def test_all_persisted(n, tmp_path):
    store = _run(n, str(tmp_path))
    assert store.count() == n


def test_exact_id_set(tmp_path):
    store = _run(33, str(tmp_path))
    assert store.ids() == {f"e{i}" for i in range(33)}


def test_blank_lines_tolerated(tmp_path):
    events_path = os.path.join(str(tmp_path), "events.jsonl")
    db_path = os.path.join(str(tmp_path), "events.db")
    with open(events_path, "w") as f:
        f.write(json.dumps({"id": "a", "payload": {}}) + "\n")
        f.write("\n")  # a blank line in the stream
        f.write(json.dumps({"id": "b", "payload": {}}) + "\n")
    worker.process(events_path, db_path)
    assert EventStore(db_path).count() == 2
