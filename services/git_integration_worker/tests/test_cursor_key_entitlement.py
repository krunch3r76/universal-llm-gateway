"""Admit refuses a dead Cursor key before HOME mint or bridge spawn."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from services.git_integration_worker.app import create_app
from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_key_entitlement import (
    apply_probe_verdict,
    clear_entitlement_cache,
    is_not_entitled,
    note_plan_required,
    probe_configured_keys,
)
from services.git_integration_worker.routes import cursor_sdk as route_mod
from services.git_integration_worker.routes.cursor_sdk import _stamp_cursor_auth


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    clear_entitlement_cache()
    yield
    clear_entitlement_cache()
    CursorDispatchLedger._instance = None


def _dispatch_body() -> dict[str, Any]:
    return {
        "thread_id": "1558",
        "model": "cursor/composer-2.5",
        "dispatch_id": "disp-dead-key",
        "execution_id": "exec-dead-key",
        "message": "hello",
    }


def test_dead_key_refuses_admit_before_home_or_bridge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provenance = "env:CURSOR_API_KEY"
    apply_probe_verdict(provenance, "not_entitled")
    assert is_not_entitled(provenance)

    home = MagicMock()
    bridge = MagicMock()
    monkeypatch.setattr(route_mod, "setup_cursor_dispatch_home", home)
    monkeypatch.setattr(route_mod, "launch_sdk_bridge", bridge)
    monkeypatch.setattr(
        route_mod,
        "validate_dispatch_context",
        lambda *_a, **_k: {"cursor_auth_source": provenance},
    )

    client = TestClient(create_app())
    resp = client.post("/api/v1/cursor/dispatch", json=_dispatch_body())
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == "cursor_key_not_entitled"
    assert body["data"]["cursor_auth"] == provenance
    home.assert_not_called()
    bridge.assert_not_called()


def test_startup_probe_marks_key_and_successful_probe_clears(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    monkeypatch.delenv("CURSOR_API_KEY_OTHER_MODELS", raising=False)
    gateway = tmp_path / ".gateway"
    gateway.mkdir()
    (gateway / "secrets.env").write_text(
        "CURSOR_API_KEY=primary-secret\n", encoding="utf-8"
    )

    def _dead(_provenance: str, *, api_key: str | None) -> str:
        assert api_key == "primary-secret"
        return "not_entitled"

    rows = probe_configured_keys(probe=_dead, real_home=tmp_path)
    assert rows[0]["verdict"] == "not_entitled"
    assert is_not_entitled("env:CURSOR_API_KEY")

    def _live(_provenance: str, *, api_key: str | None) -> str:
        del api_key
        return "entitled"

    probe_configured_keys(probe=_live, real_home=tmp_path)
    assert is_not_entitled("env:CURSOR_API_KEY") is False


def test_plan_required_marks_key_and_bridge_abort_envelope_carries_auth() -> None:
    marked = note_plan_required(
        "Error: plan_required for this account",
        provenance="env:CURSOR_API_KEY",
    )
    assert marked is True
    assert is_not_entitled("env:CURSOR_API_KEY")

    env_data: dict[str, Any] = {}
    _stamp_cursor_auth(
        env_data,
        {"cursor_auth": "env:CURSOR_API_KEY"},
        "bridge abort plan_required",
    )
    assert env_data["cursor_auth"] == "env:CURSOR_API_KEY"


def test_rotated_api_key_clears_not_entitled_mark() -> None:
    note_plan_required(
        "Error: plan_required for this account",
        provenance="env:CURSOR_API_KEY",
        api_key="old-key",
    )
    assert is_not_entitled("env:CURSOR_API_KEY", api_key="old-key") is True
    assert is_not_entitled("env:CURSOR_API_KEY", api_key="new-key") is False
