"""Regression: _post_impl injects after_turn=0 into POST /threads/with-turn.

The grokbuild post guard (4f9fefbd) rejected ``after_turn is not None``, so the
always-on injection blocked every MCP post. Exercise the live _post_impl path,
not the guard helpers in isolation (8fe3f74d gap).
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

from agent_bus_store import create_app
from agent_bus_store.auth import require_token
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent))

from tools.agent_bus import _post_impl  # noqa: E402


def _relay_via_client(client: TestClient):
    def relay(
        service: str,
        method: str,
        path: str,
        body: dict | None = None,
        token: str | None = None,
    ) -> dict:
        del token
        if service != "agent-bus" or method != "POST" or path != "/threads/with-turn":
            return {"error": f"unexpected relay: {service} {method} {path}"}
        resp = client.post(path, json=body)
        if resp.status_code >= 400:
            detail = resp.json().get("detail", resp.text)
            return {
                "error": f"HTTP {resp.status_code}",
                "status_code": resp.status_code,
                "detail": detail,
            }
        return resp.json()

    return relay


def test_post_impl_succeeds_with_injected_after_turn_zero(tmp_path) -> None:
    app = create_app(db_path=str(tmp_path / "bus.db"))
    app.dependency_overrides[require_token] = lambda: None
    with TestClient(app) as client:
        with patch("tools.agent_bus._relay", side_effect=_relay_via_client(client)):
            result = _post_impl(
                slug="bug3-post-impl-regression",
                to="cursor",
                subject="regression",
                body="via _post_impl",
                from_agent="cursor",
                summary=None,
            )
    assert "error" not in result
    assert result.get("thread", {}).get("id")
    assert result.get("turn", {}).get("turn_number") == 1
