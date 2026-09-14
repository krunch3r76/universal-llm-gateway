"""Guard the late-binding import discipline for patchable safety gates (a:33608).

`from module import func` copies the function object into the importing module's
namespace at import time. A test that patches `module.func` afterwards therefore
changes nothing the caller sees: the call site still holds the original object.
When the function is a safety gate, the gate is silently disarmed and no test
fails -- that is exactly how the live-bridge restart-deferral gate was lost.

`import module` + `module.func()` resolves the attribute per call, so patching
works and the gate stays testable.

This trap has recurred three times, twice after an explicit written warning, so
the rule is enforced mechanically here rather than left to review. Ruff's
banned-api cannot express it: it matches the qualified name and so flags the
correct `module.func()` call just as readily as the incorrect import.

To register a newly patchable function, add it to LATE_BOUND_FUNCTIONS.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_GIW_ROOT = Path(__file__).resolve().parents[1]
_REPO_ROOT = _GIW_ROOT.parents[1]

# (module path relative to services/git_integration_worker, function name).
# These are patched by tests and/or gate destructive actions, so their call
# sites must resolve late.
LATE_BOUND_FUNCTIONS: frozenset[tuple[str, str]] = frozenset(
    {
        ("cursor_sdk_restart_bridge_gate", "defer_restart_for_live_bridges"),
        ("cursor_sdk_restart_bridge_gate", "live_bridge_blocks_restart"),
        ("cursor_sdk_restart_bridge_gate", "count_live_operator_bridges"),
        ("cursor_sdk_orphan", "live_bridge_occupancy"),
        ("cursor_sdk_orphan", "owned_live_bridge_occupancy"),
        ("cursor_sdk_orphan", "sweep_unowned_bridges"),
        ("cursor_sdk_branch_divergence", "measure_divergence"),
        ("cursor_sdk_branch_debt_reconcile", "commit_exists"),
    }
)

_BANNED_BY_MODULE: dict[str, set[str]] = {}
for _module, _func in LATE_BOUND_FUNCTIONS:
    _BANNED_BY_MODULE.setdefault(_module, set()).add(_func)


def _is_test_module(path: Path) -> bool:
    return "tests" in path.parts or path.name.startswith("test_")


def _python_sources() -> list[Path]:
    """Production modules only.

    A test that imports one of these functions to invoke it directly is not a
    gate call site and has nothing to patch through; the rule constrains the
    production callers whose behaviour a test needs to be able to redirect.
    """
    roots = (
        _GIW_ROOT,
        _REPO_ROOT / "scripts" / "model_manager",
        _REPO_ROOT / "libs",
    )
    files: list[Path] = []
    for root in roots:
        if root.is_dir():
            files.extend(
                p
                for p in root.rglob("*.py")
                if "__pycache__" not in p.parts and not _is_test_module(p)
            )
    return files


def _offending_imports(tree: ast.AST) -> list[tuple[str, str, int]]:
    """Every `from <registered module> import <registered function>` in *tree*."""
    found: list[tuple[str, str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.module is None:
            continue
        tail = node.module.rsplit(".", 1)[-1]
        banned = _BANNED_BY_MODULE.get(tail)
        if not banned:
            continue
        for alias in node.names:
            if alias.name in banned:
                found.append((tail, alias.name, node.lineno))
    return found


def test_patchable_gates_are_never_bound_by_from_import() -> None:
    violations: list[str] = []
    for path in _python_sources():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeDecodeError):  # not ours to police
            continue
        for module, func, lineno in _offending_imports(tree):
            violations.append(
                f"{path.relative_to(_REPO_ROOT)}:{lineno} imports {func} from {module}"
            )
    assert not violations, (
        "These call sites bind a patchable safety gate at import time, so "
        "patching the module cannot reach them and the gate can be disarmed "
        "without any test failing (a:33608).\n\n"
        + "\n".join(sorted(violations))
        + "\n\nUse `from services.git_integration_worker import <module>` and "
        "call `<module>.<func>(...)` instead."
    )


def test_the_guard_actually_detects_the_bad_shape() -> None:
    """A guard that cannot fail is not a guard.

    Without this, a bug in the AST walk would make the rule vacuously green and
    the next regression would land unnoticed.
    """
    module, func = sorted(LATE_BOUND_FUNCTIONS)[0]
    bad = ast.parse(f"from services.git_integration_worker.{module} import {func}\n")
    assert _offending_imports(bad) == [(module, func, 1)]

    good = ast.parse(
        f"from services.git_integration_worker import {module}\n{module}.{func}()\n"
    )
    assert _offending_imports(good) == []


@pytest.mark.parametrize(("module", "func"), sorted(LATE_BOUND_FUNCTIONS))
def test_registered_functions_still_exist(module: str, func: str) -> None:
    """Keep the registry honest as the code moves.

    A renamed or deleted function would otherwise leave a dead entry that
    silently protects nothing.
    """
    source = (_GIW_ROOT / f"{module}.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }
    assert func in names, f"{module}.{func} is registered but no longer defined"
