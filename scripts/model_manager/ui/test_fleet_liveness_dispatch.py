"""Manage JSON-RPC contract tests for the fleet liveness method."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.model_manager.ui import api_dispatch


def test_fleet_liveness_dispatches_read_only_snapshot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    expected = {"schema_version": 1, "services": []}
    monkeypatch.setattr(
        api_dispatch,
        "build_snapshot",
        lambda root, state: expected,
    )
    ctl = SimpleNamespace(root=tmp_path, service_state=object())
    result = asyncio.run(api_dispatch.execute(ctl, "fleet_liveness", "", {}))
    assert result is expected


def test_fleet_liveness_rejects_parameters() -> None:
    ctl = SimpleNamespace(root=Path("."), service_state=object())
    with pytest.raises(ValueError, match="accepts no parameters"):
        asyncio.run(
            api_dispatch.execute(
                ctl, "fleet_liveness", "", {"force": True}
            )
        )
