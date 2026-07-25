"""SHA-256 helpers — stdlib-only leaf; no intra-package dependencies.

Extracted here so file_editor.py can compute file hashes without
importing tools.filesystem, which would create a circular dependency
(filesystem → _ops_text → file_editor → filesystem).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

# Citation prefixes callers may attach when composing assertion/spec hashes.
# ``read_sha256`` / ``written_sha256`` are bare hex; CAS guards accept either form.
_SHA256_CITATION_PREFIXES = ("sha256:", "spec_sha256:")


def normalize_sha256_hex(value: str) -> str:
    """Return lowercase bare hex; strip optional ``sha256:`` / ``spec_sha256:``."""
    stripped = value.strip()
    lower = stripped.lower()
    for prefix in _SHA256_CITATION_PREFIXES:
        if lower.startswith(prefix):
            return stripped[len(prefix) :].lower()
    return lower


def sha256_hex_equal(left: str | None, right: str | None) -> bool:
    """True when both sides denote the same digest after prefix normalization."""
    if left is None or right is None:
        return left is right
    return normalize_sha256_hex(left) == normalize_sha256_hex(right)


def format_sha256_uri(value: str) -> str:
    """Canonical ``sha256:<hex>`` form for mismatch echo (friction a:26153)."""
    return f"sha256:{normalize_sha256_hex(value)}"


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
