"""Audit logging utilities for APEX pipeline replayability."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class AuditStore:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def append(self, snapshot_id: str, record: dict[str, Any]) -> Path:
        file_path = self.output_dir / f"{snapshot_id}.jsonl"
        with file_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
        return file_path

    def load(self, snapshot_id: str) -> list[dict[str, Any]]:
        file_path = self.output_dir / f"{snapshot_id}.jsonl"
        if not file_path.exists():
            return []
        rows: list[dict[str, Any]] = []
        with file_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                rows.append(json.loads(line))
        return rows
