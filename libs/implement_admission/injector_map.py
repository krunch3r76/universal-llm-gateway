"""Seat-facing injector nomination, distinct from CONSUMERS import-nomination.

``CONSUMERS`` answers who imports a libs module (harvest ledger / process
load). ``INJECTORS`` answers who serves that module's content to a seat
(paste, briefing assembly). Harvest mints verified restart rows from the
union so a briefing land cannot recycle only the importer and leave the
injector stale.

Callers: ``rows_from_lib_consumers`` / ``episode_residue._actions_for_path``.
"""

from __future__ import annotations

import ast
import importlib
from collections.abc import Iterator
from pathlib import Path

from implement_admission.consumer_import_verify import (
    DerivedSource,
    check_consumers_declarations,
    is_lib_test_module_path,
    repo_root,
    residue_actions_for_lib_consumers,
    verify_consumer_import,
)

_LIBS_DIR = "libs"
_INJECTORS_ATTR = "INJECTORS"
_CONSUMERS_ATTR = "CONSUMERS"


def _literal_str_tuple(node: ast.AST | None) -> tuple[str, ...] | None:
    """Return string elements from a tuple/list literal, else ``None``."""
    if not isinstance(node, (ast.Tuple, ast.List)):
        return None
    values: list[str] = []
    for elt in node.elts:
        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
            values.append(elt.value)
        else:
            return None
    return tuple(values)


def tuple_declared_in_source(text: str, name: str) -> tuple[str, ...] | None:
    """Parse a module-level ``NAME = (…)`` / annotated assign from *text*.

    Returns ``None`` when the module does not declare *name*. Only the
    declaring file's own assignment is considered.
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == name:
                return _literal_str_tuple(node.value)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return _literal_str_tuple(node.value)
    return None


def injectors_declared_in_source(text: str) -> tuple[str, ...] | None:
    """Parse a module-level ``INJECTORS = (…)`` assignment from source *text*.

    Returns ``None`` when the file does not declare the tuple.
    """
    return tuple_declared_in_source(text, _INJECTORS_ATTR)


def _attr_tuple_for_lib_path(path: str, attr: str) -> tuple[str, ...] | None:
    """Import a string-tuple module attr from a libs path, walking up packages."""
    if is_lib_test_module_path(path):
        return None
    if not path.startswith("libs/") or not path.endswith(".py"):
        return None
    rel = path[len("libs/") : -3]
    parts = rel.replace("/", ".").split(".")
    for end in range(len(parts), 0, -1):
        module_path = ".".join(parts[:end])
        try:
            module = importlib.import_module(module_path)
        except ImportError:
            continue
        value = getattr(module, attr, None)
        if value is None:
            continue
        if isinstance(value, (list, tuple)):
            return tuple(str(item) for item in value)
    return None


def injectors_for_lib_path(path: str) -> tuple[str, ...] | None:
    """Return declared ``INJECTORS`` slugs for a libs module, else ``None``.

    Walks up package parents the same way CONSUMERS lookup does.
    """
    return _attr_tuple_for_lib_path(path, _INJECTORS_ATTR)


def nominations_for_lib_path(
    path: str,
) -> tuple[tuple[str, DerivedSource], ...]:
    """Return ``(slug, derived)`` pairs: injectors first, then CONSUMERS.

    Duplicate slugs keep the injector tag — seat-facing recycle is the
    question harvest got wrong when it minted only the importer.
    """
    injectors = injectors_for_lib_path(path) or ()
    consumers = _attr_tuple_for_lib_path(path, _CONSUMERS_ATTR) or ()
    seen: set[str] = set()
    out: list[tuple[str, DerivedSource]] = []
    for slug in injectors:
        if slug in seen:
            continue
        seen.add(slug)
        out.append((slug, "injectors"))
    for slug in consumers:
        if slug in seen:
            continue
        seen.add(slug)
        out.append((slug, "consumers"))
    return tuple(out)


def iter_injectors_declarations(
    root: Path | None = None,
) -> Iterator[tuple[str, tuple[str, ...]]]:
    """Yield ``(libs/…path, injectors)`` for each libs module that declares INJECTORS."""
    base = root if root is not None else repo_root()
    libs = base / _LIBS_DIR
    if not libs.is_dir():
        return
    for path in sorted(libs.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(base).as_posix()
        if is_lib_test_module_path(rel):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        injectors = injectors_declared_in_source(text)
        if injectors is None:
            continue
        yield rel, injectors


def check_injectors_declarations(
    *,
    root: Path | None = None,
) -> list[str]:
    """Return failure lines for INJECTORS slugs that do not reach their file."""
    base = root if root is not None else repo_root()
    failures: list[str] = []
    for path, injectors in iter_injectors_declarations(base):
        for slug in injectors:
            status = verify_consumer_import(slug, path, root=base)
            if status != "verified":
                failures.append(
                    f"{path}: INJECTORS slug {slug!r} is import_path:{status} "
                    f"(declaring module must be reached)"
                )
    return failures


def check_nomination_declarations(
    *,
    root: Path | None = None,
) -> list[str]:
    """Return combined authorship-time failures for CONSUMERS and INJECTORS tuples together."""
    return [
        *check_consumers_declarations(root=root),
        *check_injectors_declarations(root=root),
    ]


def residue_actions_for_nominations(
    path: str,
    nominations: tuple[tuple[str, DerivedSource], ...],
    *,
    root: Path | None = None,
) -> tuple[str, ...]:
    """Build RESIDUE lines for injector then consumer nominations on *path*."""
    grouped: dict[DerivedSource, list[str]] = {}
    for slug, derived in nominations:
        grouped.setdefault(derived, []).append(slug)
    lines: list[str] = []
    order: tuple[DerivedSource, ...] = ("injectors", "consumers")
    for derived in order:
        slugs = grouped.get(derived)
        if not slugs:
            continue
        lines.extend(
            residue_actions_for_lib_consumers(
                path, tuple(slugs), root=root, derived=derived
            )
        )
    return tuple(lines)


__all__ = [
    "check_injectors_declarations",
    "check_nomination_declarations",
    "injectors_declared_in_source",
    "injectors_for_lib_path",
    "iter_injectors_declarations",
    "nominations_for_lib_path",
    "residue_actions_for_nominations",
    "tuple_declared_in_source",
]
