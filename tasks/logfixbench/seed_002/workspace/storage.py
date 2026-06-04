import sqlite3


class EventStore:
    """SQLite-backed event store."""

    def __init__(self, path: str):
        self.conn = sqlite3.connect(path)
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS events ("
            "  rowid_   INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  event_id TEXT,"
            "  payload  TEXT"
            ")"
        )
        self.conn.commit()

    def persist(self, event_id: str, payload: str) -> None:
        # NOTE: inserts a new row unconditionally.
        self.conn.execute(
            "INSERT INTO events (event_id, payload) VALUES (?, ?)",
            (event_id, payload),
        )
        self.conn.commit()

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]

    def ids(self) -> set:
        return {row[0] for row in self.conn.execute("SELECT event_id FROM events")}
