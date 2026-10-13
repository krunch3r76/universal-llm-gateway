"""Unit tests for CHARTER_RECONCILE_INTERVAL_S env resolution."""

from __future__ import annotations

import pytest

from scripts.model_manager.ui.controller.charter_runner.kernel.interval import (
    DEFAULT_RECONCILE_INTERVAL_S,
    reconcile_interval_from_env,
)


@pytest.mark.offline
def test_reconcile_interval_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CHARTER_RECONCILE_INTERVAL_S", raising=False)
    assert reconcile_interval_from_env() == DEFAULT_RECONCILE_INTERVAL_S


@pytest.mark.offline
def test_reconcile_interval_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHARTER_RECONCILE_INTERVAL_S", "420")
    assert reconcile_interval_from_env() == 420.0


@pytest.mark.offline
def test_reconcile_interval_clamps_below_minimum(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CHARTER_RECONCILE_INTERVAL_S", "10")
    assert reconcile_interval_from_env() == 60.0
