"""Data access layer: CRUD over each registered resource."""
from datetime import datetime, timezone

from .resources import RESOURCES
from .validation import validate_payload, ValidationError


def _now():
    return datetime.now(timezone.utc).isoformat()


class Repo:
    def __init__(self, db, key):
        self.db = db
        self.table = RESOURCES[key]["table"]

    def list(self):
        rows = self.db.query(f"SELECT * FROM {self.table} ORDER BY id DESC")
        return [dict(r) for r in rows]

    def create(self, payload):
        data = validate_payload(payload)
        cur = self.db.execute(
            f"INSERT INTO {self.table} (title, notes, done, created_at) VALUES (?, ?, ?, ?)",
            (data["title"], data.get("notes", ""), 1 if data.get("done") else 0, _now()))
        return self.get(cur.lastrowid)

    def get(self, item_id):
        rows = self.db.query(f"SELECT * FROM {self.table} WHERE id = ?", (item_id,))
        return dict(rows[0]) if rows else None

    def update(self, item_id, payload):
        if self.get(item_id) is None:
            return None
        data = validate_payload(payload, partial=True)
        if not data:
            raise ValidationError("body", "no updatable fields supplied")
        if "done" in data:
            data["done"] = 1 if data["done"] else 0
        assignments = ", ".join(f"{field} = ?" for field in data)
        values = list(data.values()) + [item_id]
        self.db.execute(
            f"UPDATE {self.table} SET {assignments} WHERE id = ?", values)
        return self.get(item_id)

    def delete(self, item_id):
        if self.get(item_id) is None:
            return False
        self.db.execute(f"DELETE FROM {self.table} WHERE id = ?", (item_id,))
        return True
