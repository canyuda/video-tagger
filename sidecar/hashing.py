"""Content hashing and cache-key derivation (docs/03 §2: cache key =
file_hash + stage + version). Storage is keyed by file_hash — original file
names are display-only."""

from __future__ import annotations

import hashlib
from pathlib import Path

_CHUNK = 1 << 20  # 1 MiB


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def cache_key(file_hash: str, stage: str, version: str | int) -> str:
    """Deterministic cache key; version = taxonomy version for classify stage,
    prompt/model version for other stages."""
    joined = f"{file_hash}|{stage}|{version}"
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()
