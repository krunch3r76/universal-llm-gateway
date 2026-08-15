"""Path-level serving coverage — nominated, declared-unserved, or unmapped.

``serving_services_for_lib`` returns ``()`` both when a lib has no
``serves_libs`` row and when the union of serves + CONSUMERS + INJECTORS is
empty. Harvest then emits the same ``libs_touched`` line that tests treat as
"correctly nothing to restart". This module is the distinction the serving
field cannot make: ``unmapped`` is loud and does not mint a restart;
``unserved`` is an explicit declaration that no manage restart is owed.

Callers: harvest ``episode_residue._actions_for_path`` and structured
``rows_from_lib_consumers``. The census is a query surface, not an admit gate.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from implement_admission.consumer_import_verify import (
    is_lib_test_module_path,
    repo_root,
)
from implement_admission.injector_map import nominations_for_lib_path
from implement_admission.service_lib_ownership import (
    lib_name_for_path,
    unserved_libs,
)

ServingCoverage = Literal["nominated", "unserved", "unmapped"]

_LIBS_DIR = "libs"
UNMAPPED_PREFIX = "unmapped_serving:"


def path_serving_coverage(path: str) -> ServingCoverage:
    """Classify a ``libs/`` path after the three nomination sources are consulted.

    ``nominated`` wins whenever any source named a slug. ``unserved`` requires
    membership in ``UNSERVED_LIBS``. Everything else that is a libs path is
    ``unmapped`` — including a lib nobody has classified yet.
    """
    if nominations_for_lib_path(path):
        return "nominated"
    name = lib_name_for_path(path)
    if name is not None and name in unserved_libs():
        return "unserved"
    return "unmapped"


def unmapped_serving_line(path: str) -> str:
    """Lead-visible line for an undeclared serving process — not a restart mint."""
    return (
        f"{UNMAPPED_PREFIX} {path} — no serves_libs, CONSUMERS, or INJECTORS; "
        "undeclared serving process (distinct from declared-unserved)"
    )


def unserved_line(path: str) -> str:
    """Return the labeled correctly-nothing residue line for a declared-unserved lib.

    Must not share the ``unmapped_serving:`` prefix — that prefix is the
    undeclared-serving alarm, not a successful empty classification.
    """
    return (
        f"libs_touched: {path} — declared unserved; no manage restart nominated"
    )


def residue_for_empty_nominations(path: str) -> tuple[str, ...]:
    """Residue when ``nominations_for_lib_path`` returned empty.

    Unmapped is loud. Unserved is the distinguishable correctly-nothing case.
    """
    coverage = path_serving_coverage(path)
    if coverage == "unserved":
        return (unserved_line(path),)
    if coverage == "unmapped":
        return (unmapped_serving_line(path),)
    return ()


def unmapped_top_level_libs(root: Path | None = None) -> tuple[str, ...]:
    """Worker-hosted census of top-level ``libs/`` names with unmapped coverage.

    Not a fail-closed admit or CI gate. A nonempty result is the honesty
    surface: these names have no ``serves_libs`` row, no package-root
    CONSUMERS/INJECTORS, and are not in ``UNSERVED_LIBS``.
    """
    base = root if root is not None else repo_root()
    libs = base / _LIBS_DIR
    if not libs.is_dir():
        return ()
    names: list[str] = []
    for child in sorted(libs.iterdir()):
        if child.name.startswith("_") or child.name.startswith("."):
            continue
        if child.is_dir() and (child / "__init__.py").is_file():
            path = f"{_LIBS_DIR}/{child.name}/__init__.py"
        elif child.is_file() and child.suffix == ".py":
            path = f"{_LIBS_DIR}/{child.name}"
        else:
            continue
        if is_lib_test_module_path(path):
            continue
        if path_serving_coverage(path) != "unmapped":
            continue
        name = lib_name_for_path(path)
        if name:
            names.append(name)
    return tuple(names)


__all__ = [
    "ServingCoverage",
    "UNMAPPED_PREFIX",
    "path_serving_coverage",
    "residue_for_empty_nominations",
    "unmapped_serving_line",
    "unmapped_top_level_libs",
    "unserved_line",
]
