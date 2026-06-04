import sqlite3


class EventStore:
    """Idempotent event store keyed by event_id."""

    def __init__(self, path: str):
        self.conn = sqlite3.connect(path)
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS events ("
            "  event_id TEXT PRIMARY KEY,"
            "  payload  TEXT"
            ")"
        )
        self.conn.commit()

    def persist(self, event_id: str, payload: str) -> None:
        # Idempotent: the same event_id is never stored twice.
        self.conn.execute(
            "INSERT OR IGNORE INTO events (event_id, payload) VALUES (?, ?)",
            (event_id, payload),
        )
        self.conn.commit()

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]

    def ids(self) -> set:
        return {row[0] for row in self.conn.execute("SELECT event_id FROM events")}
