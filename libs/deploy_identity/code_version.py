"""Resolve the running process code version once, at process start.

Resolution order: ``ULG_CODE_VERSION`` env override, then deploy stamp line 2
(written at source-sync time), then ``git rev-parse HEAD``.

The git fallback is only attributable to the running process while the process
is young: a checkout HEAD read minutes after start describes the checkout, not
the loaded code. Past that window the git fallback is withheld and the value
resolves to ``unknown`` rather than reporting a commit the process never
loaded. The module resolves eagerly at import so services that import it during
startup seal an attributable value before the checkout can move underneath them.
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

# A checkout HEAD read this long after exec is no longer evidence about the
# loaded code. Service startup is seconds; this window is deliberately loose.
_ATTRIBUTION_WINDOW_S = 60.0


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


def process_age_s() -> float | None:
    """Seconds since this process was exec'd, or None where /proc is absent."""
    try:
        stat = Path("/proc/self/stat").read_text(encoding="utf-8")
        uptime_raw = Path("/proc/uptime").read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        # comm (field 2) is parenthesised and may contain spaces.
        fields = stat[stat.rindex(")") + 1 :].split()
        starttime_ticks = float(fields[19])
        ticks_per_s = float(os.sysconf("SC_CLK_TCK"))
        uptime_s = float(uptime_raw.split()[0])
    except (ValueError, IndexError, OSError):
        return None
    if ticks_per_s <= 0:
        return None
    return max(0.0, uptime_s - starttime_ticks / ticks_per_s)


def _git_head_attributable() -> bool:
    """True while a checkout HEAD read is still evidence about this process."""
    age = process_age_s()
    if age is None:
        return True
    return age <= _ATTRIBUTION_WINDOW_S


@lru_cache(maxsize=1)
def resolve_code_version() -> str:
    """Return the process-start SHA or ``unknown`` when resolution fails."""
    override = os.environ.get("ULG_CODE_VERSION", "").strip()
    if override:
        return override
    stamped = _read_stamp_sha(_stamp_path())
    if stamped:
        return stamped
    if not _git_head_attributable():
        logger.warning(
            "code_version withheld: first resolution %.0fs after process start, "
            "checkout HEAD is not evidence about the loaded code",
            process_age_s() or -1.0,
        )
        return _UNKNOWN
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


# Seal an attributable value at import. Services import deploy_identity during
# startup, so this runs inside the attribution window; a later first call would
# not.
resolve_code_version()


__all__ = [
    "normalize_code_ref",
    "process_age_s",
    "reset_code_version_cache_for_tests",
    "resolve_code_version",
]
