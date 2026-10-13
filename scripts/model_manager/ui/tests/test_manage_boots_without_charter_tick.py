"""S1 — manage boot path must not start the charter runner tick."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


@pytest.mark.offline
def test_import_manage_app_succeeds() -> None:
    import scripts.model_manager.ui.app  # noqa: F401


@pytest.mark.offline
def test_manage_app_has_no_charter_runner_tick() -> None:
    from scripts.model_manager.ui import app as app_mod

    source = Path(app_mod.__file__).read_text(encoding="utf-8")
    assert "CharterRunnerTickLoop" not in source
    retired_pkg = ".".join(["controller", "charter_runner"])
    assert retired_pkg not in source

    tree = ast.parse(source)
    activity_literals: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and node.value == "charter_tick":
            activity_literals.add("charter_tick")
    assert activity_literals == set(), "app.py must not register activity charter_tick"
