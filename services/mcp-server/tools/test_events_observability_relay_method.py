"""MCP observability relay uses POST for sql and GET for catalog members."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import httpx

from tools.events import _query_event_service


class _RecordingClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def __enter__(self) -> _RecordingClient:
        return self

    def __exit__(self, *_a: object) -> bool:
        return False

    def get(self, path: str, *, params: dict[str, Any] | None = None) -> httpx.Response:
        self.calls.append(("GET", path, {"params": params or {}}))
        return httpx.Response(200, json={"ok": True})

    def post(self, path: str, *, json: dict[str, Any] | None = None) -> httpx.Response:
        self.calls.append(("POST", path, {"json": json or {}}))
        return httpx.Response(200, json={"rows": []})


def test_observability_sql_relayed_as_post_with_body() -> None:
    client = _RecordingClient()
    with patch("event_store.query_client.make_sync_client", return_value=client):
        _query_event_service("sql", {"sql": "SELECT 1", "limit": 5})
    assert client.calls[0][0:2] == ("POST", "/api/v1/observability/sql")
    assert client.calls[0][2]["json"]["sql"] == "SELECT 1"
    assert client.calls[0][2]["json"]["limit"] == 5


def test_observability_member_stays_get() -> None:
    client = _RecordingClient()
    with patch("event_store.query_client.make_sync_client", return_value=client):
        _query_event_service("recent-failures", {"limit": 3})
    assert client.calls[0][0:2] == ("GET", "/api/v1/observability/recent-failures")
    assert client.calls[0][2]["params"] == [("limit", "3")]
