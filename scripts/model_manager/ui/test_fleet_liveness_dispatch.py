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
        lambda root, state, **kwargs: expected,
    )
    ctl = SimpleNamespace(root=tmp_path, service_state=object())
    result = asyncio.run(api_dispatch.execute(ctl, "fleet_liveness", "", {}))
    assert result is expected


def test_fleet_liveness_rejects_unknown_parameters() -> None:
    ctl = SimpleNamespace(root=Path("."), service_state=object())
    with pytest.raises(ValueError, match="accepts only code_ref, activation_validation_id"):
        asyncio.run(
            api_dispatch.execute(
                ctl, "fleet_liveness", "", {"force": True}
            )
        )


def test_fleet_liveness_forwards_activation_validation_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}

    def _capture(root, state, **kwargs):
        captured.update(kwargs)
        return {"schema_version": 1}

    monkeypatch.setattr(api_dispatch, "build_snapshot", _capture)
    ctl = SimpleNamespace(root=tmp_path, service_state=object())
    asyncio.run(
        api_dispatch.execute(
            ctl,
            "fleet_liveness",
            "",
            {
                "code_ref": "abc",
                "activation_validation_id": "val-1",
            },
        )
    )
    assert captured == {
        "code_ref": "abc",
        "activation_validation_id": "val-1",
    }
