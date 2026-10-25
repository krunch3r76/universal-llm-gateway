"""House journal POST client (fail-open append)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from bus_watch.house_journal import append_house_action

pytestmark = pytest.mark.offline


def test_append_uses_bearer_headers_without_env_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_BUS_TOKEN", raising=False)
    client = MagicMock()
    resp = MagicMock()
    resp.status_code = 202
    client.post.return_value = resp
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=False)
    headers = {"Authorization": "Bearer from-yaml"}
    with (
        patch("bus_watch.house_journal.agent_bus_bearer_headers", return_value=headers),
        patch("bus_watch.house_journal.make_sync_client", return_value=client),
    ):
        assert append_house_action("10479", "gate_swap", "ticker", "friction gate") is True
    client.post.assert_called_once()
    assert client.post.call_args.kwargs["headers"] == headers


def test_append_skipped_when_bearer_headers_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_BUS_TOKEN", raising=False)
    with patch("bus_watch.house_journal.agent_bus_bearer_headers", return_value={}):
        with patch("bus_watch.house_journal.make_sync_client") as mk:
            assert append_house_action("10479", "gate_swap", "ticker", "why") is False
            mk.assert_not_called()
