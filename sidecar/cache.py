"""Tiny JSON file cache for pipeline stage artifacts.

Layout: <root>/<file_hash[:2]>/<file_hash>/<stage>-v<version>.json
Sharding by hash prefix keeps directories small. Atomic writes (tmp + rename).
Phase 1 moves this into SQLite `artifacts`; the CLI keeps it file-based for
inspectability while iterating on hints.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


class FileCache:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, file_hash: str, stage: str, version: str | int) -> Path:
        return self.root / file_hash[:2] / file_hash / f"{stage}-v{version}.json"

    def get(self, file_hash: str, stage: str, version: str | int) -> Any | None:
        path = self._path(file_hash, stage, version)
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # Corrupt cache entries are treated as absent — recompute. The
            # cache is a pure optimization; never fail the pipeline on it.
            return None

    def put(self, file_hash: str, stage: str, version: str | int, payload: Any) -> Path:
        path = self._path(file_hash, stage, version)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
        return path
