"""Offline tests for :8 endpoint auto-subscribe helper."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from bus_watch.cursor_xvfb_subscription import (
    cursor_xvfb_subscription_body,
    parse_ide_holder_agent_id,
    subscribe_cursor_xvfb_from_holder,
    subscribe_cursor_xvfb_house,
)

pytestmark = pytest.mark.offline


def test_parse_ide_holder_agent_id() -> None:
    tid = "db36e9f8-e76f-49e7-bd57-c9c4ffa88195"
    assert parse_ide_holder_agent_id(f"ide:{tid}") == tid
    assert parse_ide_holder_agent_id("ide:bad") is None


def test_subscription_body_title_default() -> None:
    body = cursor_xvfb_subscription_body(
        thread_scope="18374", agent_id="db36e9f8-e76f-49e7-bd57-c9c4ffa88195"
    )
    assert body["display"] == "8"
    assert body["title"] == "18374 "


def test_subscribe_cursor_xvfb_house_posts() -> None:
    client = MagicMock()
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"appended": True}
    client.post.return_value = resp
    out = subscribe_cursor_xvfb_house(
        "18374",
        "db36e9f8-e76f-49e7-bd57-c9c4ffa88195",
        client=client,
    )
    assert out["ok"] is True
    client.post.assert_called_once()
    body = client.post.call_args.kwargs["json"]
    assert body["thread_scope"] == "18374"
    assert body["agent_id"] == "db36e9f8-e76f-49e7-bd57-c9c4ffa88195"


def test_subscribe_from_holder_rejects_non_ide() -> None:
    out = subscribe_cursor_xvfb_from_holder("18374", "cursor-sdk")
    assert out["ok"] is False
    assert out["reason"] == "holder_not_ide_uuid"


def test_subscribe_survives_bus_config_failure() -> None:
    from unittest.mock import patch

    with patch("bus_watch.cursor_xvfb_subscription._bus", side_effect=SystemExit(1)):
        out = subscribe_cursor_xvfb_house("18374", "db36e9f8-e76f-49e7-bd57-c9c4ffa88195")
    assert out["ok"] is False
    assert "subscription_failed" in out.get("reason", "")
