"""Resolve the running process code version once per interpreter lifetime.

Resolution order: ``ULG_CODE_VERSION`` env override, then deploy stamp line 2
(written at source-sync time), then ``git rev-parse HEAD``. Cached module-wide
so liveness and health probes stay cheap.
"""

from __future__ import annotations

import os
import re
import subprocess
from functools import lru_cache
from pathlib import Path

from universal_logging import get_logger
from universal_workspace import get_workspace_root

logger = get_logger(__name__)

_UNKNOWN = "unknown"
_SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
_DEFAULT_STAMP_PATH = Path("/app/.source_sync_stamp")


def _stamp_path() -> Path:
    override = os.environ.get("ULG_DEPLOY_STAMP_PATH", "").strip()
    return Path(override) if override else _DEFAULT_STAMP_PATH


def _read_stamp_sha(path: Path) -> str | None:
    """Return line-2 SHA from a source-sync stamp when present."""
    try:
        if not path.is_file():
            return None
        lines = path.read_text(encoding="utf-8").strip().splitlines()
    except OSError as exc:
        logger.warning("code_version stamp read failed (%s): %s", path, exc)
        return None
    if len(lines) < 2:
        return None
    candidate = lines[1].strip().lower()
    return candidate if _SHA40_RE.fullmatch(candidate) else None


@lru_cache(maxsize=1)
def resolve_code_version() -> str:
    """Return the process-start SHA or ``unknown`` when resolution fails."""
    override = os.environ.get("ULG_CODE_VERSION", "").strip()
    if override:
        return override
    stamped = _read_stamp_sha(_stamp_path())
    if stamped:
        return stamped
    try:
        root = get_workspace_root()
    except RuntimeError as exc:
        logger.warning("code_version workspace root unavailable: %s", exc)
        return _UNKNOWN
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5.0,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("code_version git rev-parse failed: %s", exc)
        return _UNKNOWN
    sha = proc.stdout.strip()
    return sha or _UNKNOWN


def normalize_code_ref(code_ref: str) -> str:
    """Resolve symbolic refs (HEAD) to a concrete SHA at mint time."""
    ref = str(code_ref or "").strip()
    if ref.upper() == "HEAD":
        return resolve_code_version()
    return ref


def reset_code_version_cache_for_tests() -> None:
    """Clear the module-level LRU cache so tests can re-resolve version."""
    resolve_code_version.cache_clear()


__all__ = [
    "normalize_code_ref",
    "reset_code_version_cache_for_tests",
    "resolve_code_version",
]
