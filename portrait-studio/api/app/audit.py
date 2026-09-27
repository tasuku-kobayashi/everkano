"""Audit log: one JSON line per generation job in `logs/audit.jsonl` + a sidecar JSON next to every output image."""

from __future__ import annotations

import json
import threading
from collections import deque
from pathlib import Path
from typing import Any


class AuditLog:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, entry: dict[str, Any]) -> None:
        line = json.dumps(entry, ensure_ascii=False, default=str)
        with self._lock, self.path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def tail(self, limit: int) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        with self._lock, self.path.open("r", encoding="utf-8") as f:
            lines = deque(f, maxlen=limit)
        items: list[dict[str, Any]] = []
        for raw in lines:
            line = raw.strip()
            if not line:
                continue
            try:
                items.append(json.loads(line))
            except ValueError:
                items.append({"raw": line})
        items.reverse()  # newest first
        return items

    def count_lines(self) -> int:
        if not self.path.is_file():
            return 0
        with self._lock, self.path.open("r", encoding="utf-8") as f:
            return sum(1 for line in f if line.strip())

    @staticmethod
    def write_sidecar(image_path: Path, entry: dict[str, Any]) -> Path:
        sidecar = image_path.with_suffix(".json")
        sidecar.write_text(json.dumps(entry, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return sidecar
