"""Read-only manage JSON-RPC client — socket down and method lock."""

from __future__ import annotations

import pytest
from manage_readonly_jsonrpc import call_readonly, fleet_liveness

pytestmark = pytest.mark.offline


def test_non_fleet_method_does_not_connect(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_a, **_k):
        raise AssertionError("socket exchange must not run")

    monkeypatch.setattr("manage_readonly_jsonrpc._exchange", _boom)
    out = call_readonly("sync_restart", {"service": "cortex_api"})
    assert out["error"].startswith("read-only manage client refuses")


def test_fleet_liveness_unwraps_result(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[dict] = []

    def _exchange(body, *, timeout):
        captured.append(body)
        return {"jsonrpc": "2.0", "id": 1, "result": {"schema_version": 1}}

    monkeypatch.setattr("manage_readonly_jsonrpc._exchange", _exchange)
    out = fleet_liveness(code_ref="abc", services=["cortex-api"])
    assert out == {"schema_version": 1}
    assert captured[0]["method"] == "fleet_liveness"
    assert captured[0]["params"]["services"] == ["cortex-api"]


def test_socket_missing_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "manage_readonly_jsonrpc._exchange",
        lambda body, *, timeout: {"error": "manage socket not found: /missing"},
    )
    out = fleet_liveness(code_ref="abc", services=["cortex-api"])
    assert "error" in out


def test_rpc_error_envelope_is_error_dict(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "manage_readonly_jsonrpc._exchange",
        lambda body, *, timeout: {
            "jsonrpc": "2.0",
            "id": 1,
            "error": {"code": -32601, "message": "method not found"},
        },
    )
    out = fleet_liveness(code_ref="abc", services=["mcp"])
    assert out == {"error": "method not found"}
