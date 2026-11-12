"""C3: ledger lives in spawn_ledger — repo must not import it from cursor_bridge."""

from __future__ import annotations

import ast
from collections.abc import Iterable
from pathlib import Path

import pytest
from charter_runner_store.archive_pkg import import_archived

repo_root = import_archived("propagation_libs_closure").repo_root

_CURSOR_BRIDGE_LEDGER_MODULE = "cursor_bridge.spawn_endpoint_house_ledger"

_STARGATE_LEDGER_PATHS = (
    "services/universal-stargate/systems/frontier_consult/route_team_dispatch.py",
    "services/universal-stargate/systems/frontier_consult/generate_wrap.py",
    "services/universal-stargate/systems/frontier_consult/cdp_generate.py",
    "pipelines/team_dispatch_admit/v1/handlers/assemble.py",
)

_SCAN_ROOTS = (
    Path("libs"),
    Path("services"),
    Path("pipelines"),
    Path("scripts"),
)

_STARGATE_PIPELINE_ROOTS = (
    Path("services/universal-stargate/systems/frontier_consult"),
    Path("pipelines/team_dispatch_admit"),
)


def _py_files_under(root: Path) -> Iterable[Path]:
    if not root.is_dir():
        return
    for path in root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        yield path


def _importlib_string_literals(tree: ast.AST) -> list[str]:
    literals: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (
            isinstance(func, ast.Attribute)
            and func.attr == "import_module"
            and isinstance(func.value, ast.Name)
            and func.value.id == "importlib"
        ):
            continue
        if not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            literals.append(first.value)
    return literals


def _ledger_hits_in_tree(rel: Path, tree: ast.AST) -> list[str]:
    hits: list[str] = []
    rel_s = str(rel)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.name
                if name == _CURSOR_BRIDGE_LEDGER_MODULE or (
                    name.startswith("cursor_bridge")
                    and name.endswith("spawn_endpoint_house_ledger")
                ):
                    hits.append(f"{rel_s}:import {name}")
        if isinstance(node, ast.ImportFrom) and node.module:
            mod = node.module
            if mod == _CURSOR_BRIDGE_LEDGER_MODULE:
                names = ", ".join(a.name for a in node.names)
                hits.append(f"{rel_s}:from {mod} import {names}")
            if mod.startswith("cursor_bridge"):
                for alias in node.names:
                    if alias.name == "spawn_endpoint_house_ledger":
                        hits.append(
                            f"{rel_s}:from {mod} import {alias.name}",
                        )
    for lit in _importlib_string_literals(tree):
        if lit == _CURSOR_BRIDGE_LEDGER_MODULE or (
            lit.startswith("cursor_bridge") and "spawn_endpoint_house_ledger" in lit
        ):
            hits.append(f"{rel_s}:importlib {lit}")
    return hits


def _ledger_hits_in_file(path: Path) -> list[str]:
    root = repo_root()
    rel = path.relative_to(root)
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError):
        return [f"{rel}:unreadable"]
    return _ledger_hits_in_tree(rel, tree)


def _collect_hits(roots: Iterable[Path]) -> list[str]:
    root = repo_root()
    out: list[str] = []
    for rel in roots:
        for path in _py_files_under(root / rel):
            out.extend(_ledger_hits_in_file(path))
    return sorted(set(out))


@pytest.mark.offline
def test_repo_wide_no_cursor_bridge_spawn_endpoint_house_ledger_import() -> None:
    assert _collect_hits(_SCAN_ROOTS) == []


@pytest.mark.offline
def test_stargate_pipeline_admit_trees_avoid_cursor_bridge_ledger_import() -> None:
    assert _collect_hits(_STARGATE_PIPELINE_ROOTS) == []


@pytest.mark.offline
@pytest.mark.parametrize("rel_path", _STARGATE_LEDGER_PATHS)
def test_stargate_spawn_ledger_paths_import_spawn_ledger(rel_path: str) -> None:
    path = repo_root() / rel_path
    text = path.read_text(encoding="utf-8")
    assert _ledger_hits_in_file(path) == [], rel_path
    assert "spawn_ledger" in text, rel_path
