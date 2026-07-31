"""Default limit and honesty fields on agent_bus threads dispatcher."""

from __future__ import annotations

from unittest.mock import patch

from tools.agent_bus.threads import _DEFAULT_THREAD_LIMIT, _threads_impl


def test_threads_default_limit_forwarded() -> None:
    captured: dict[str, object] = {}

    def _relay(service: str, method: str, path: str) -> dict[str, object]:
        captured["path"] = path
        return {"threads": [{"id": "1"}]}

    with patch("tools.agent_bus.threads.relay", side_effect=_relay):
        result = _threads_impl(status="active")

    assert f"limit={_DEFAULT_THREAD_LIMIT}" in str(captured["path"])
    assert result["limit_applied"] == 50
    assert result["truncated"] is False


def test_threads_explicit_limit_forwarded() -> None:
    captured: dict[str, object] = {}

    def _relay(service: str, method: str, path: str) -> dict[str, object]:
        captured["path"] = path
        return {"threads": []}

    with patch("tools.agent_bus.threads.relay", side_effect=_relay):
        result = _threads_impl(status="active", limit=200)

    assert "limit=200" in str(captured["path"])
    assert result["limit_applied"] == 200
