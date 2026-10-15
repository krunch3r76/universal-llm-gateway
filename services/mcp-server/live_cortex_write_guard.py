"""Live cortex-root write predicate shared by the mcp-server pytest guard."""

from __future__ import annotations

import os
from pathlib import Path


def live_cortex_files_roots() -> tuple[Path, ...]:
    """Every root a test must not write: env, container, and home, together.

    An exported ``CORTEX_FILES_ROOT`` does not drop ``/data/files`` or
    ``~/mcp-data/files``. ``/data/files`` is included only when that directory
    exists.
    """
    roots: list[Path] = []
    raw = os.environ.get("CORTEX_FILES_ROOT")
    if raw:
        roots.append(Path(raw).resolve())
    container = Path("/data/files")
    if container.is_dir():
        roots.append(container.resolve())
    roots.append((Path.home() / "mcp-data" / "files").resolve())
    unique: list[Path] = []
    for root in roots:
        if root not in unique:
            unique.append(root)
    return tuple(unique)


_LIVE_CORTEX_FILES_ROOTS = live_cortex_files_roots()


def under_live_cortex(path: object) -> bool:
    """True when *path* is a filesystem path under any guarded root."""
    if isinstance(path, int) or not isinstance(path, (str, Path)):
        return False
    try:
        resolved = Path(path).resolve()
    except (OSError, RuntimeError, ValueError):
        return False
    for root in _LIVE_CORTEX_FILES_ROOTS:
        try:
            resolved.relative_to(root)
        except ValueError:
            continue
        return True
    return False


def write_mode(mode: object) -> bool:
    """True when an ``open`` mode string can create or modify a file."""
    text = mode if isinstance(mode, str) else ""
    return any(flag in text for flag in ("w", "a", "x", "+"))
