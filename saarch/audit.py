"""Append-only JSONL audit trail: every stage, decision, and check leaves a record."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict


class AuditLog:
    def __init__(self, workdir: Path) -> None:
        self.path = Path(workdir) / "audit.jsonl"

    def append(self, action: str, **details: Any) -> None:
        entry: Dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z") or time.time(),
            "action": action,
        }
        entry.update(details)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, default=str) + "\n")

    def entries(self):
        if not self.path.exists():
            return []
        rows = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                rows.append(json.loads(line))
        return rows
