"""SHA-256 helpers — stdlib-only leaf; no intra-package dependencies.

Extracted here so file_editor.py can compute file hashes without
importing tools.filesystem, which would create a circular dependency
(filesystem → _ops_text → file_editor → filesystem).
"""

from __future__ import annotations

import hashlib
from pathlib import Path


def sha256_hex_of_bytes(data: bytes) -> str:
    """Return lowercase hex SHA-256 of *data* (no ``sha256:`` prefix)."""
    return hashlib.sha256(data).hexdigest()


def sha256_hex_of_file(path: Path) -> str:
    """Return lowercase hex SHA-256 of on-disk file bytes (no ``sha256:`` prefix)."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_of_file(path: Path) -> str | None:
    """Return ``sha256:<hex>`` of file bytes, or None when *path* is absent."""
    if not path.is_file():
        return None
    return f"sha256:{sha256_hex_of_file(path)}"
