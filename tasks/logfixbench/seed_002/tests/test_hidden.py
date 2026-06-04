import json
import os
import sys

import pytest

# The agent's (possibly modified) code lives at /workspace.
sys.path.insert(0, "/workspace")

import worker  # noqa: E402
from storage import EventStore  # noqa: E402


def _write(path, events):
    with open(path, "w") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


def _run(events, tmp):
    events_path = os.path.join(tmp, "events.jsonl")
    db_path = os.path.join(tmp, "events.db")
    _write(events_path, events)
    worker.process(events_path, db_path)
    return EventStore(db_path)


def test_duplicates_in_stream_persist_once(tmp_path):
    # 10 distinct events; the first 4 are delivered a second time.
    events = [{"id": f"e{i}", "payload": {"n": i}} for i in range(10)]
    events += [{"id": f"e{i}", "payload": {"n": i}} for i in range(4)]
    store = _run(events, str(tmp_path))
    assert store.count() == 10
    assert store.ids() == {f"e{i}" for i in range(10)}


def test_rerun_does_not_duplicate(tmp_path):
    events = [{"id": f"e{i}", "payload": {"n": i}} for i in range(20)]
    events_path = os.path.join(str(tmp_path), "events.jsonl")
    db_path = os.path.join(str(tmp_path), "events.db")
    _write(events_path, events)
    worker.process(events_path, db_path)
    worker.process(events_path, db_path)
    assert EventStore(db_path).count() == 20


@pytest.mark.parametrize("n,dups", [(50, 10), (33, 33), (5, 1)])
def test_distinct_count(n, dups, tmp_path):
    events = [{"id": f"e{i}", "payload": {"n": i}} for i in range(n)]
    events += [{"id": f"e{i}", "payload": {"n": i}} for i in range(dups)]
    store = _run(events, str(tmp_path))
    assert store.count() == n
