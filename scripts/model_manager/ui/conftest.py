"""Hermetic guard for manage UI tests (charter harvest, etc.)."""

from __future__ import annotations

import pytest

pytest_plugins = ["scripts.model_manager.testing.conftest"]

from scripts.model_manager.ui.controller.tests.conftest import (  # noqa: E402
    stub_process_live_probe,
)


@pytest.fixture(autouse=True)
def _ui_stub_process_live_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    stub_process_live_probe(monkeypatch)
