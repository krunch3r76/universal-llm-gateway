"""Inverse authorship gate: a services/ import of an unmapped lib must fail.

Predicate (G1 / turn 88): every nontest ``services/`` import of a lib whose
nomination union is empty fails. Not a frozen census list — a lib created
next week with no CONSUMERS and a fresh importer fails from birth. The 13
census names with zero nontest service imports never fire this predicate.

Walks the same import grammar ``verify_consumer_import`` uses (whole-file,
including function-local imports) except ``if TYPE_CHECKING`` bodies.
Tests are skipped. Callers: ``check_nomination_declarations`` (Lane-A
offline CI) and the imported-unmapped census helper.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

from implement_admission.consumer_import_verify import repo_root
from implement_admission.injector_map import nominations_for_lib_path
from implement_admission.service_lib_ownership import (
    lib_name_for_path,
    slug_for_service_path,
)

_LIBS_DIR = "libs"
_SERVICES_DIR = "services"


def is_service_test_module_path(path: str) -> bool:
    """True for service test modules (same skip class as the omission census)."""
    text = str(path or "").replace("\\", "/")
    if not text.startswith(f"{_SERVICES_DIR}/") or not text.endswith(".py"):
        return False
    name = text.rsplit("/", 1)[-1]
    if name.startswith("test_") or name.endswith("_test.py") or name == "conftest.py":
        return True
    return any(part in {"test", "tests"} for part in text.split("/"))


def _is_type_checking_test(node: ast.expr) -> bool:
    """True for ``TYPE_CHECKING`` / ``typing.TYPE_CHECKING`` if-tests."""
    if isinstance(node, ast.Name) and node.id == "TYPE_CHECKING":
        return True
    return isinstance(node, ast.Attribute) and node.attr == "TYPE_CHECKING"


def _imported_modules(tree: ast.AST) -> Iterator[str]:
    """Yield absolute dotted names imported outside TYPE_CHECKING blocks."""

    def walk(stmts: list[ast.stmt]) -> Iterator[str]:
        for node in stmts:
            if isinstance(node, ast.If):
                if _is_type_checking_test(node.test):
                    continue
                yield from walk(node.body)
                yield from walk(node.orelse)
            elif isinstance(node, ast.Try):
                yield from walk(node.body)
                for handler in node.handlers:
                    yield from walk(handler.body)
                yield from walk(node.orelse)
                yield from walk(node.finalbody)
            elif isinstance(node, ast.With):
                yield from walk(node.body)
            elif isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ):
                yield from walk(node.body)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name:
                        yield alias.name
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    continue
                if node.module:
                    yield node.module

    yield from walk(list(getattr(tree, "body", [])))


def _top_level_lib_names(root: Path) -> frozenset[str]:
    """Return top-level ``libs/`` package and module names at *root*."""
    libs = root / _LIBS_DIR
    if not libs.is_dir():
        return frozenset()
    names: set[str] = set()
    for child in libs.iterdir():
        if child.name.startswith("_") or child.name.startswith("."):
            continue
        if child.is_dir() and (child / "__init__.py").is_file():
            names.add(child.name)
        elif child.is_file() and child.suffix == ".py":
            names.add(child.stem)
    return frozenset(names)


def _lib_path_for_import(root: Path, module: str) -> str | None:
    """Resolve a dotted import to a repo-relative ``libs/…py`` path, else ``None``."""
    parts = [p for p in module.split(".") if p]
    if not parts:
        return None
    libs = root / _LIBS_DIR
    as_mod = (
        libs.joinpath(*parts[:-1], f"{parts[-1]}.py")
        if len(parts) > 1
        else libs / f"{parts[0]}.py"
    )
    if as_mod.is_file():
        return as_mod.relative_to(root).as_posix()
    as_init = libs.joinpath(*parts, "__init__.py")
    if as_init.is_file():
        return as_init.relative_to(root).as_posix()
    while len(parts) > 1:
        parts.pop()
        as_mod = (
            libs.joinpath(*parts[:-1], f"{parts[-1]}.py")
            if len(parts) > 1
            else libs / f"{parts[0]}.py"
        )
        if as_mod.is_file():
            return as_mod.relative_to(root).as_posix()
        as_init = libs.joinpath(*parts, "__init__.py")
        if as_init.is_file():
            return as_init.relative_to(root).as_posix()
    return None


def iter_imported_unmapped_pairs(
    *,
    root: Path | None = None,
) -> Iterator[tuple[str, str, str, str]]:
    """Yield ``(importer, slug, lib_name, lib_path)`` for empty-nomination imports.

    One row per ``(importer, lib_name, slug)`` — a file that imports two
    modules of the same unmapped package reports once. Package×slug grain
    matches the omission census.
    """
    base = root if root is not None else repo_root()
    services = base / _SERVICES_DIR
    if not services.is_dir():
        return
    lib_names = _top_level_lib_names(base)
    seen: set[tuple[str, str, str]] = set()
    for path in sorted(services.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(base).as_posix()
        if is_service_test_module_path(rel):
            continue
        slug = slug_for_service_path(rel)
        if slug is None:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        for module in _imported_modules(tree):
            top = module.split(".", 1)[0]
            if top not in lib_names:
                continue
            lib_path = _lib_path_for_import(base, module)
            if lib_path is None:
                continue
            key = (rel, top, slug)
            if key in seen:
                continue
            if nominations_for_lib_path(lib_path, root=base):
                continue
            seen.add(key)
            yield rel, slug, top, lib_path


def imported_unmapped_census(
    *,
    root: Path | None = None,
) -> dict[str, object]:
    """Return package×slug census counts for empty-nomination service imports.

    Same instrument as the inverse CI. Quote these numbers; do not narrate them.
    """
    pairs = list(iter_imported_unmapped_pairs(root=root))
    pkg_svc = {(lib_name, slug) for _, slug, lib_name, _ in pairs}
    packages = {lib_name for _, _, lib_name, _ in pairs}
    slugs = {slug for _, slug, _, _ in pairs}
    return {
        "pair_count": len(pkg_svc),
        "package_count": len(packages),
        "slug_count": len(slugs),
        "importer_row_count": len(pairs),
        "packages": tuple(sorted(packages)),
        "pairs": tuple(sorted(pkg_svc)),
    }


def check_unmapped_importers(
    *,
    root: Path | None = None,
) -> list[str]:
    """Return failure lines for nontest service imports of unmapped libs.

    Empty list means the predicate holds: every live ``services/`` import
    reaches a nonempty nomination union (or is not a libs import).
    """
    failures: list[str] = []
    for rel, slug, lib_name, lib_path in iter_imported_unmapped_pairs(root=root):
        name = lib_name_for_path(lib_path) or lib_name
        failures.append(
            f"{rel}: imports unmapped lib {name!r} ({lib_path}); "
            f"nomination union empty — slug {slug!r} would be omitted at harvest"
        )
    return failures


__all__ = [
    "check_unmapped_importers",
    "imported_unmapped_census",
    "is_service_test_module_path",
    "iter_imported_unmapped_pairs",
]
