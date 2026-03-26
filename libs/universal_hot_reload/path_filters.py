"""
Path-aware include/exclude helpers for file watchers.

Patterns are matched with fnmatch against the path relative to the watch root.
Bare filename globs also match against the basename to preserve existing
filename-only configs while enabling subtree excludes such as ``trading/**``.
"""

from __future__ import annotations

from collections.abc import Sequence
from fnmatch import fnmatch
from pathlib import Path


def matches_watch_exclude(
    path: str | Path,
    *,
    watch_root: str | Path,
    patterns: Sequence[str],
) -> bool:
    """Return True when a path matches any watch exclude pattern.

    Matching surface:
    - watch-root-relative POSIX path for path-aware patterns like ``trading/**``
    - basename for legacy filename globs like ``CORPUS_MANIFEST.md``
    """

    if not patterns:
        return False

    resolved_path = Path(path).expanduser().resolve()
    resolved_root = Path(watch_root).expanduser().resolve()
    relative_path = _relative_match_path(resolved_path, resolved_root)
    basename = resolved_path.name

    for raw_pattern in patterns:
        pattern = _normalize_pattern(raw_pattern)
        if not pattern:
            continue
        if fnmatch(relative_path, pattern):
            return True
        if "/" not in pattern and fnmatch(basename, pattern):
            return True
    return False


def _normalize_pattern(pattern: str) -> str:
    return pattern.strip().replace("\\", "/").lstrip("./")


def _relative_match_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.name
