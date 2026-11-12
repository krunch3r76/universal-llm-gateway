"""ULG checkout root resolution for container ``PROJECT_ROOT`` mounts."""

from __future__ import annotations

import os
from pathlib import Path


def ulg_repo_root(*, libs_anchor: Path) -> Path:
    """Return the hub checkout root.

    ``libs_anchor`` is any ``libs/<package>/…`` source file used for the
    ``__file__.parents[2]`` fallback when ``PROJECT_ROOT`` is unset or invalid.
    """
    project_root = Path(os.environ.get("PROJECT_ROOT", "")).expanduser()
    if project_root.is_dir():
        if (project_root / "libs" / "bus_watch").is_dir():
            return project_root
        nested = project_root / "universal-llm-gateway"
        if nested.is_dir() and (nested / "libs" / "bus_watch").is_dir():
            return nested
    return libs_anchor.resolve().parents[2]
