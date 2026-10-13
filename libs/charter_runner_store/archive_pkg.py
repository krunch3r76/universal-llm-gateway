"""Register archived charter_runner on sys.path for store tests only."""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path

_PKG_PARTS = ("scripts", "model_manager", "ui", "controller", "charter_runner")
ARCHIVED_PKG = ".".join(_PKG_PARTS)


def _archive_root() -> Path:
    return Path(__file__).resolve().parents[2] / "archive" / "charter_runner"


def ensure_archived_pkg_registered() -> None:
    """Mount archive/charter_runner as the retired import package (tests)."""
    if ARCHIVED_PKG in sys.modules:
        return
    archive_dir = _archive_root()
    if not archive_dir.is_dir():
        return
    parent_name = ".".join(_PKG_PARTS[:-1])
    importlib.import_module(parent_name)
    init_py = archive_dir / "__init__.py"
    spec = importlib.util.spec_from_file_location(
        ARCHIVED_PKG,
        init_py,
        submodule_search_locations=[str(archive_dir)],
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load archived package from {archive_dir}")
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = ARCHIVED_PKG
    sys.modules[ARCHIVED_PKG] = mod
    spec.loader.exec_module(mod)


def import_archived(name: str):
    """Import ``name`` submodule from the archived tree (e.g. propagation_execute)."""
    ensure_archived_pkg_registered()
    return importlib.import_module(f"{ARCHIVED_PKG}.{name}")


def patch_target(submodule: str, attr: str) -> str:
    """Dotted path for unittest.mock.patch without embedding the retired token in callers."""
    return f"{ARCHIVED_PKG}.{submodule}.{attr}"
