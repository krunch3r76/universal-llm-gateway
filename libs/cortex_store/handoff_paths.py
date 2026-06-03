"""Shared path normalization and hashing for handoff source files."""

from __future__ import annotations

import hashlib
from pathlib import Path


def normalize_handoff_source_path(source_path: str | None) -> str | None:
    """Strip a leading ``cortex:`` scheme + slashes from a cortex file path."""
    if not source_path:
        return None
    cleaned = source_path.strip()
    if cleaned.startswith("cortex:"):
        cleaned = cleaned[len("cortex:") :]
    return cleaned.lstrip("/") or None


def sha256_text(text: str) -> str:
    return f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"


def sha256_bytes(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def read_sandboxed_source_text(files_root: Path, source_path: str | None) -> str | None:
    """Return sandboxed file text or None when path escapes or file is unreadable."""
    rel = normalize_handoff_source_path(source_path)
    if rel is None:
        return None
    try:
        abs_path = (files_root / rel).resolve()
        abs_path.relative_to(files_root.resolve())
        return abs_path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None
