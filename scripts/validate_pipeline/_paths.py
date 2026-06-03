"""Import path bootstrap for the validate-pipeline CLI."""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def ensure_import_paths() -> Path:
    """Ensure repo root, libs, and stargate are on ``sys.path``.

    PEP-420 ``from libs.*`` imports resolve against the repository root, not
    ``libs/`` alone. Stargate's ``systems.pipeline`` package pulls in executor
    code that imports ``libs.universal_concurrency`` at module load time.
    """
    for path in (
        _PROJECT_ROOT,
        _PROJECT_ROOT / "libs",
        _PROJECT_ROOT / "services" / "universal-stargate",
    ):
        path_str = str(path)
        if path.is_dir() and path_str not in sys.path:
            sys.path.insert(0, path_str)
    return _PROJECT_ROOT
