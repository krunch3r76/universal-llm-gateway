"""Content-addressed retention for cortex fs overwrites (item-15 / AC-15b)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tools._durable_write import durable_write_bytes
from tools._hashing import normalize_sha256_hex, sha256_hex_of_bytes, sha256_hex_of_file

from . import _paths
from ._share_uri_response import attach_dual_carry


def content_store_root() -> Path:
    """Root directory for content-addressed retention blobs."""
    return _paths.SANDBOX_ROOT / ".content-store" / "sha256"


def store_path_for_hex(sha256_hex: str) -> Path:
    """Return on-disk path for a bare lowercase sha256 hex digest."""
    normalized = normalize_sha256_hex(sha256_hex)
    if len(normalized) != 64:
        raise ValueError(f"Invalid sha256 hex digest: {sha256_hex!r}")
    return content_store_root() / normalized[:2] / normalized


def retain_bytes(data: bytes) -> str:
    """Idempotently persist *data* by hash; return bare sha256 hex."""
    sha = sha256_hex_of_bytes(data)
    dest = store_path_for_hex(sha)
    if dest.is_file() and sha256_hex_of_file(dest) == sha:
        return sha
    dest.parent.mkdir(parents=True, exist_ok=True)
    durable_write_bytes(dest, data)
    return sha


def retain_file(path: Path) -> str | None:
    """Retain on-disk file bytes when present; return bare sha256 hex."""
    if not path.is_file():
        return None
    return retain_bytes(path.read_bytes())


def resolve_sha256_impl(sha256: str) -> dict[str, Any]:
    """Return whether a cited digest still resolves in the content store."""
    normalized = normalize_sha256_hex(sha256)
    if len(normalized) != 64:
        return {
            "resolved": False,
            "sha256": normalized,
            "source": None,
            "stale": True,
            "error": f"Invalid sha256 hex digest: {sha256!r}",
        }
    store_path = store_path_for_hex(normalized)
    if store_path.is_file() and sha256_hex_of_file(store_path) == normalized:
        rel = store_path.relative_to(_paths.SANDBOX_ROOT).as_posix()
        return attach_dual_carry(
            {
                "resolved": True,
                "sha256": normalized,
                "source": "content_store",
                "stale": False,
            },
            sandbox="cortex",
            rel_path=rel,
        )
    return {
        "resolved": False,
        "sha256": normalized,
        "source": None,
        "stale": True,
    }
