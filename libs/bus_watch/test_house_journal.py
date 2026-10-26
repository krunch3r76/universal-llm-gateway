"""House journal POST client (fail-open append)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from bus_watch.house_journal import append_house_action

pytestmark = pytest.mark.offline


def test_append_posts_using_mcp_yaml_when_env_unset(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:  # noqa: ANN001
    monkeypatch.delenv("AGENT_BUS_TOKEN", raising=False)
    mcp = tmp_path / "mcp.yaml"
    mcp.write_text("AGENT_BUS_TOKEN: yaml-tok\n", encoding="utf-8")
    monkeypatch.setattr("bus_watch.digest_budget._MCP_YAML", mcp)
    client = MagicMock()
    resp = MagicMock()
    resp.status_code = 202
    client.post.return_value = resp
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=False)
    with patch("bus_watch.house_journal.make_sync_client", return_value=client):
        assert append_house_action("10479", "gate_swap", "ticker", "friction gate") is True
    client.post.assert_called_once()
    assert client.post.call_args.kwargs["headers"] == {
        "Authorization": "Bearer yaml-tok",
    }


def test_append_skipped_when_bearer_headers_empty(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:  # noqa: ANN001
    monkeypatch.delenv("AGENT_BUS_TOKEN", raising=False)
    mcp = tmp_path / "mcp.yaml"
    mcp.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr("bus_watch.digest_budget._MCP_YAML", mcp)
    with patch("bus_watch.house_journal.make_sync_client") as mk:
        assert append_house_action("10479", "gate_swap", "ticker", "why") is False
        mk.assert_not_called()
