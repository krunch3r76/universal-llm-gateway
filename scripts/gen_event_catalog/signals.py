"""Transitive resolution of @event_factory signal constants.

Follows same-module constants, intra-module Name aliases, and relative/absolute
intra-repo imports (cycle-guarded) to the string literal that ultimately defines
a signal. Pure functions + memo cache; no event bus / async.
"""

from __future__ import annotations

import ast
from functools import cache, lru_cache
from pathlib import Path

from .extract import PROJECT_ROOT, WALK_ROOTS


@lru_cache(maxsize=1)
def _search_roots() -> tuple[Path, ...]:
    """Roots for absolute (level==0) intra-repo imports: PROJECT_ROOT + every src/ dir."""
    roots = [PROJECT_ROOT]
    for r in WALK_ROOTS:
        roots.extend(p for p in (PROJECT_ROOT / r).rglob("src") if p.is_dir())
    return tuple(roots)


@cache
def _parse(path: Path) -> ast.Module | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError):
        return None


@cache
def _maps(path: Path) -> tuple[dict[str, str], dict[str, str]]:
    """(NAME -> str constant, NAME -> alias NAME) module-level bindings."""
    consts: dict[str, str] = {}
    aliases: dict[str, str] = {}
    tree = _parse(path)
    if tree is None:
        return consts, aliases
    for stmt in tree.body:
        targets: list[ast.Name] = []
        value: ast.expr | None = None
        if isinstance(stmt, ast.Assign):
            targets = [t for t in stmt.targets if isinstance(t, ast.Name)]
            value = stmt.value
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            targets = [stmt.target]
            value = stmt.value
        if not targets or value is None:
            continue
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            for t in targets:
                consts[t.id] = value.value
        elif isinstance(value, ast.Name):
            for t in targets:
                aliases[t.id] = value.id
    return consts, aliases


def _module_file(importing: Path, node: ast.ImportFrom) -> Path | None:
    parts = (node.module or "").split(".") if node.module else []
    if node.level >= 1:
        base = importing.parent
        for _ in range(node.level - 1):
            base = base.parent
        candidates = [base.joinpath(*parts)]
    else:
        candidates = [root.joinpath(*parts) for root in _search_roots()]
    for c in candidates:
        if c.with_suffix(".py").exists():
            return c.with_suffix(".py")
        if (c / "__init__.py").exists():
            return c / "__init__.py"
    return None


def resolve_signal(
    name: str, path: Path, seen: frozenset[tuple[str, str]] | None = None
) -> str | None:
    seen = seen or frozenset()
    key = (str(path), name)
    if key in seen:
        return None
    seen = seen | {key}
    consts, aliases = _maps(path)
    if name in consts:
        return consts[name]
    if name in aliases:
        return resolve_signal(aliases[name], path, seen)
    tree = _parse(path)
    if tree is None:
        return None
    for stmt in tree.body:
        if not isinstance(stmt, ast.ImportFrom):
            continue
        for alias in stmt.names:
            if (alias.asname or alias.name) == name:
                target = _module_file(path, stmt)
                if target is not None:
                    return resolve_signal(alias.name, target, seen)
    return None
