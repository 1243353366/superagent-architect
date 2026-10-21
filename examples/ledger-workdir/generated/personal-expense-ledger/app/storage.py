"""SQLite persistence with versioned migrations.

Migrations are explicit and ordered; ``migrate()`` applies any that have not
run yet and records the applied version in the ``schema_version`` table.
"""
import sqlite3
import threading

SCHEMA_VERSION = 1

MIGRATIONS = {
    1: [
        "CREATE TABLE IF NOT EXISTS expense (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '', done INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL)",
        "CREATE INDEX IF NOT EXISTS idx_expense_created ON expense (created_at)",
        "CREATE TABLE IF NOT EXISTS budget (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '', done INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL)",
        "CREATE INDEX IF NOT EXISTS idx_budget_created ON budget (created_at)",
    ],
}


class Database:
    """Thread-safe single-file database handle."""

    def __init__(self, path):
        self.path = str(path)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._lock = threading.Lock()

    def execute(self, sql, params=()):
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def query(self, sql, params=()):
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def migrate(self):
        with self._lock:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL DEFAULT 0)")
            row = self._conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
            current = row["v"] or 0
            for version in sorted(MIGRATIONS):
                if version > current:
                    for statement in MIGRATIONS[version]:
                        self._conn.execute(statement)
                    self._conn.execute("UPDATE schema_version SET version = ? "
                                       "WHERE version = ?", (version, current))
                    self._conn.commit()

    def close(self):
        with self._lock:
            self._conn.close()
