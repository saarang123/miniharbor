import json
import os
import sys

import pytest

# The agent's (possibly modified) code lives at /workspace.
sys.path.insert(0, "/workspace")

import worker  # noqa: E402
from storage import EventStore  # noqa: E402


def _make_events(n):
    return [{"id": f"e{i}", "payload": {"n": i}} for i in range(n)]


def _run(n, tmp):
    events_path = os.path.join(tmp, "events.jsonl")
    db_path = os.path.join(tmp, "events.db")
    with open(events_path, "w") as f:
        for e in _make_events(n):
            f.write(json.dumps(e) + "\n")
    worker.process(events_path, db_path)
    return EventStore(db_path)


# Mix of sizes: exact multiples of BATCH_SIZE (pass even with the bug) and
# non-multiples (fail with the bug). Partial credit comes from this spread.
@pytest.mark.parametrize("n", [7, 10, 25, 33, 100])
def test_all_persisted(n, tmp_path):
    store = _run(n, str(tmp_path))
    assert store.count() == n


@pytest.mark.parametrize("n", [25, 33])
def test_exact_id_set(n, tmp_path):
    store = _run(n, str(tmp_path))
    assert store.ids() == {f"e{i}" for i in range(n)}


def test_idempotent_rerun(tmp_path):
    # Processing the same input twice must not change the count.
    events_path = os.path.join(str(tmp_path), "events.jsonl")
    db_path = os.path.join(str(tmp_path), "events.db")
    with open(events_path, "w") as f:
        for e in _make_events(15):
            f.write(json.dumps(e) + "\n")
    worker.process(events_path, db_path)
    worker.process(events_path, db_path)
    assert EventStore(db_path).count() == 15
