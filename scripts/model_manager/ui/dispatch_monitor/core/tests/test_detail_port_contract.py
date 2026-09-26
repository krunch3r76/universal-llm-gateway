"""Contract tests for :class:`~dispatch_monitor.core.protocols.DetailPort`."""

from __future__ import annotations

import ast
import inspect
import os

from scripts.model_manager.ui.dispatch_monitor.core.protocols import (
    DetailPort,
    TailPort,
)

_PACKAGE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PROTOCOLS_PATH = os.path.join(_PACKAGE_ROOT, "protocols.py")

_ALLOWED_PROTOCOLS_IMPORTS = frozenset({"collections.abc", "dataclasses", "typing"})

_ISOLATED_CORE_PATHS = (
    os.path.join(_PACKAGE_ROOT, "model.py"),
    *sorted(
        os.path.join(_PACKAGE_ROOT, "folds", name)
        for name in os.listdir(os.path.join(_PACKAGE_ROOT, "folds"))
        if name.endswith(".py")
    ),
)


def test_detail_port_exists_and_fetch_signature() -> None:
    """DetailPort is declared and ``fetch`` accepts ``(kind: str, key: str)``."""
    assert inspect.isclass(DetailPort)
    sig = inspect.signature(DetailPort.fetch)
    params = sig.parameters
    assert "kind" in params and "key" in params
    assert params["kind"].annotation in (str, "str")
    assert params["key"].annotation in (str, "str")
    assert not getattr(DetailPort, "_is_runtime_protocol", False)


def test_tail_port_exists_and_tail_signature() -> None:
    """TailPort is declared and ``tail`` accepts ``(kind, key, cursor)``."""
    assert inspect.isclass(TailPort)
    sig = inspect.signature(TailPort.tail)
    params = sig.parameters
    assert "kind" in params and "key" in params and "cursor" in params
    assert params["cursor"].annotation in (int, "int")
    assert not getattr(TailPort, "_is_runtime_protocol", False)


def test_detail_port_not_referenced_in_model_or_folds() -> None:
    """The fold path must not call or name DetailPort or TailPort."""
    for path in _ISOLATED_CORE_PATHS:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        rel = os.path.relpath(path, _PACKAGE_ROOT)
        assert "DetailPort" not in text, f"{rel} mentions DetailPort"
        assert "TailPort" not in text, f"{rel} mentions TailPort"
        assert ".fetch(" not in text, f"{rel} contains a .fetch( call"
        assert ".tail(" not in text, f"{rel} contains a .tail( call"


def test_protocols_imports_only_allowed_stdlib_modules() -> None:
    """``protocols.py`` imports only collections.abc, dataclasses, and typing."""
    with open(_PROTOCOLS_PATH, encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), filename=_PROTOCOLS_PATH)
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            if node.module == "__future__":
                continue
            modules.add(node.module)
    assert modules == set(_ALLOWED_PROTOCOLS_IMPORTS)
