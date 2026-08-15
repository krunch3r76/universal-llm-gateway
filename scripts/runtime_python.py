"""Resolve the repository's preferred Python interpreter before loading dependencies.

Quality gates can be launched directly from an unactivated shell, so callers
use this module to re-exec under the active virtual environment or the shared
universal environment before importing third-party packages.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def resolve_preferred_python() -> Path:
    """Return the first executable interpreter permitted for this checkout environment."""
    candidates: list[Path] = []
    virtual_env = os.environ.get("VIRTUAL_ENV")
    if virtual_env:
        candidates.append(Path(virtual_env) / "bin" / "python3")
    candidates.append(Path.home() / ".venvs" / "universal" / "bin" / "python3")
    path_python = shutil.which("python3")
    if path_python:
        candidates.append(Path(path_python))

    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    return Path(sys.executable)


def ensure_preferred_python(script_path: str) -> None:
    """Re-exec the script under the preferred interpreter when another Python launched it."""
    preferred = Path(os.path.abspath(resolve_preferred_python()))
    current = Path(os.path.abspath(sys.executable))
    if preferred == current:
        return
    os.execv(str(preferred), [str(preferred), script_path, *sys.argv[1:]])
