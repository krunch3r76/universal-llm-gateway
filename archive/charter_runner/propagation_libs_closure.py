"""Resolve ``libs/`` edits to the services that must restart to pick them up.

The path→service map in ``propagation_execute`` only knows ``services/`` prefixes,
so a closeout whose residue is ``libs/cortex_store/main.py`` resolved to nothing and
the restart never fired (arc 6386: ``/claims/burst`` stayed 404 behind a live route).

A hand-written ``libs/<name> → service`` table reproduces that bug one level down:
``predicate_form`` reaches ``cortex_api`` only through ``libs/cortex_store``, so a
direct-importer table maps it to stargate alone. Correctness needs the transitive
closure, so the map is computed from the import graph rather than maintained by hand.

Shared infrastructure is deliberately *not* special-cased here. A lib imported by
most of the fleet legitimately fans out to most of the fleet; callers that want to
refuse a fleet-wide restart apply their own breadth ceiling to the returned set.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path

_LIBS_DIR = "libs"
_IMPORT_RE = re.compile(
    r"^\s*(?:from|import)\s+(?:libs\.)?([a-z_][a-z0-9_]*)",
    re.MULTILINE,
)


def repo_root() -> Path:
    """Return the checkout root (``scripts/model_manager/ui/controller/<pkg>/`` → up 5)."""
    return Path(__file__).resolve().parents[5]


def _module_names(root: Path) -> frozenset[str]:
    """Top-level importable names under ``libs/`` (packages and single modules)."""
    libs = root / _LIBS_DIR
    if not libs.is_dir():
        return frozenset()
    names: set[str] = set()
    for entry in libs.iterdir():
        if entry.is_dir() and (entry / "__init__.py").is_file():
            names.add(entry.name)
        elif entry.suffix == ".py":
            names.add(entry.stem)
    return frozenset(names)


def _imported_libs(py_files: Iterable[Path], known: frozenset[str]) -> frozenset[str]:
    """Names from *known* imported anywhere in *py_files*."""
    found: set[str] = set()
    for path in py_files:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        found.update(name for name in _IMPORT_RE.findall(text) if name in known)
    return frozenset(found)


def _tree_files(base: Path) -> list[Path]:
    return [p for p in base.rglob("*.py") if "__pycache__" not in p.parts]


@lru_cache(maxsize=1)
def _lib_to_libs(root_str: str) -> dict[str, frozenset[str]]:
    """Direct lib→lib import edges, keyed by top-level lib name."""
    root = Path(root_str)
    known = _module_names(root)
    edges: dict[str, frozenset[str]] = {}
    for name in known:
        pkg = root / _LIBS_DIR / name
        files = _tree_files(pkg) if pkg.is_dir() else [root / _LIBS_DIR / f"{name}.py"]
        edges[name] = _imported_libs(files, known) - {name}
    return edges


def _expand(seeds: Iterable[str], edges: dict[str, frozenset[str]]) -> frozenset[str]:
    """Transitively close *seeds* over *edges*."""
    seen: set[str] = set()
    stack = list(seeds)
    while stack:
        name = stack.pop()
        if name in seen:
            continue
        seen.add(name)
        stack.extend(edges.get(name, frozenset()) - seen)
    return frozenset(seen)


@lru_cache(maxsize=1)
def _lib_to_services(root_str: str, prefixes: tuple[tuple[str, str], ...]) -> dict[str, tuple[str, ...]]:
    """Invert the import graph: lib name → service slugs needing a restart."""
    root = Path(root_str)
    known = _module_names(root)
    edges = _lib_to_libs(root_str)
    owners: dict[str, set[str]] = {}
    for prefix, slug in prefixes:
        service_dir = root / prefix.rstrip("/")
        if not service_dir.is_dir():
            continue
        direct = _imported_libs(_tree_files(service_dir), known)
        for name in _expand(direct, edges):
            owners.setdefault(name, set()).add(slug)
    return {name: tuple(sorted(slugs)) for name, slugs in owners.items()}


def lib_name_for_path(path: str) -> str | None:
    """Return the top-level ``libs/`` name a repo-relative *path* belongs to."""
    parts = Path(str(path or "")).parts
    if len(parts) < 2 or parts[0] != _LIBS_DIR or not str(path).endswith(".py"):
        return None
    second = parts[1]
    return second[:-3] if len(parts) == 2 and second.endswith(".py") else second


def services_for_lib_path(
    path: str,
    *,
    prefixes: tuple[tuple[str, str], ...],
    root: Path | None = None,
) -> tuple[str, ...]:
    """Service slugs that must restart to pick up an edit to *path*.

    Returns an empty tuple when *path* is not a ``libs/`` Python file or when no
    service imports it (a lib used only by scripts or the runner itself).
    """
    name = lib_name_for_path(path)
    if name is None:
        return ()
    base = root if root is not None else repo_root()
    return _lib_to_services(str(base), prefixes).get(name, ())


__all__ = [
    "lib_name_for_path",
    "repo_root",
    "services_for_lib_path",
]
