"""Shared overwrite retention for cortex write paths (item-15 / AC-15a, AC-15b)."""

from __future__ import annotations

from pathlib import Path

from tools._hashing import normalize_sha256_hex, sha256_of_file

from . import _paths
from ._content_store import retain_file


def _under_sandbox(dest: Path) -> bool:
    try:
        dest.resolve().relative_to(_paths.SANDBOX_ROOT.resolve())
    except ValueError:
        return False
    return True


def retain_before_overwrite(dest: Path) -> str | None:
    """Retain existing bytes and return bare replaced_sha256 when *dest* exists."""
    if not _under_sandbox(dest):
        return None
    prior_uri = sha256_of_file(dest)
    if prior_uri is None:
        return None
    retain_file(dest)
    return normalize_sha256_hex(prior_uri)
