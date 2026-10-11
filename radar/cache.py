"""Disk cache for extraction results, keyed by the file's SHA-256.

The key also carries the extraction version and model, so fixing the
prompt (Step 7) invalidates old results instead of silently reusing them,
while re-running the UI on an unchanged document never re-calls the API.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

CACHE_DIR = Path(os.environ.get("RADAR_CACHE_DIR", Path(__file__).resolve().parent.parent / ".cache" / "extractions"))


def file_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _path(sha: str, version: str, model: str, cache_dir: Path) -> Path:
    return cache_dir / f"{sha}__{version}__{model}.json"


def get(sha: str, version: str, model: str, cache_dir: Path = CACHE_DIR) -> dict | None:
    path = _path(sha, version, model, cache_dir)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None  # a corrupt entry is treated as a miss and overwritten


def put(sha: str, version: str, model: str, record: dict, cache_dir: Path = CACHE_DIR) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = _path(sha, version, model, cache_dir)
    # Write then rename, so a crash never leaves a half-written entry.
    fd, tmp = tempfile.mkstemp(dir=cache_dir, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)
    return path


def clear(cache_dir: Path = CACHE_DIR) -> int:
    if not cache_dir.exists():
        return 0
    removed = 0
    for p in cache_dir.glob("*.json"):
        p.unlink()
        removed += 1
    return removed
