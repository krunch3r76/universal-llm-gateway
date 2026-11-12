"""MCP cursor_bridge edge shim: admit payload, mapping, legacy flag-off."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from tools import _cursor_bridge_edge
from tools.cursor_bridge import _rename_tab, _wake_tab

pytestmark = pytest.mark.offline


def test_edge_path_disabled_uses_legacy_wake() -> None:
    with patch("tools.cursor_bridge.edge_glass_wake_rename_enabled", return_value=False):
        with patch("tools.cursor_bridge.relay_wake_tab") as relay:
            relay.return_value = {"ok": True, "op": "wake_tab"}
            out = _wake_tab(gui_host=":8", agent_id="a", body="line")
    relay.assert_called_once_with(gui_host=":8", agent_id="a", body="line")
    assert out["ok"] is True


def test_admit_payload_shape() -> None:
    captured: dict = {}

    def fake_post(path: str, json: dict) -> MagicMock:
        captured["path"] = path
        captured["json"] = json
        resp = MagicMock()
        resp.status_code = 202
        resp.json.return_value = {"op_id": "edgeop-test"}
        resp.content = b"{}"
        return resp

    def fake_get(path: str, params: dict | None = None) -> MagicMock:
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"ok": True, "op": "wake_tab", "op_id": "edgeop-test"}
        resp.content = b"{}"
        return resp

    client = MagicMock()
    client.post.side_effect = fake_post
    client.get.side_effect = fake_get
    client.__enter__.return_value = client
    client.__exit__.return_value = None

    with (
        patch.dict("os.environ", {"EDGE_OPS_ENABLED": "true", "EDGE_GLASS_WAKE_RENAME_ENABLED": "true"}),
        patch("tools._cursor_bridge_edge.make_sync_client", return_value=client),
    ):
        out = _cursor_bridge_edge.admit_and_await_cursor_glass(
            op="wake_tab",
            args=_cursor_bridge_edge.bridge_edge_args(
                gui_host=":8", agent_id="a", body="wake"
            ),
            idempotency_key="fixed-key",
        )
    assert out["ok"] is True
    assert captured["path"] == "/api/v1/edge/ops"
    body = captured["json"]
    assert body["kind"] == "cursor_glass"
    assert body["op"] == "wake_tab"
    assert body["idempotency_key"] == "fixed-key"
    assert body["args"]["grant"] == "bridge"
    assert body["args"]["authorization_source"] == "mcp:cursor_bridge"


def test_edge_pending_maps_ok_false() -> None:
    client = MagicMock()
    client.post.return_value = MagicMock(
        status_code=202, json=lambda: {"op_id": "x"}, content=b"{}"
    )
    client.get.return_value = MagicMock(
        status_code=200,
        json=lambda: {"ok": False, "code": "edge_pending", "op_id": "x"},
        content=b"{}",
    )
    client.__enter__.return_value = client
    client.__exit__.return_value = None

    with (
        patch.dict("os.environ", {"EDGE_OPS_ENABLED": "true", "EDGE_GLASS_WAKE_RENAME_ENABLED": "true"}),
        patch("tools.cursor_bridge.edge_glass_wake_rename_enabled", return_value=True),
        patch("tools._cursor_bridge_edge.make_sync_client", return_value=client),
    ):
        out = _wake_tab(gui_host=":8", agent_id="a", body="wake")
    assert out["ok"] is False
    assert out["code"] == "edge_pending"


def test_flag_on_wake_uses_admit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EDGE_OPS_ENABLED", "true")
    monkeypatch.setenv("EDGE_GLASS_WAKE_RENAME_ENABLED", "true")
    with patch("tools.cursor_bridge.admit_and_await_cursor_glass") as admit:
        admit.return_value = {"ok": True, "op": "wake_tab"}
        out = _wake_tab(gui_host=":8", agent_id="a", body="wake")
    admit.assert_called_once()
    assert out["ok"] is True
