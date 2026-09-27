"""GET /v1/project-ask/registry — unavailable when load_active fails."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from cdp_ask.app import create_app


@pytest.mark.offline
def test_registry_endpoint_unavailable_when_load_active_raises(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    monkeypatch.setenv("CDP_ASK_TREE_STATE_REFRESH", "0")
    app = create_app()
    fail_registry = {"armed": False}

    def _load_active():
        if fail_registry["armed"]:
            raise OSError("registry unreadable")
        return {}

    with patch("claude_bundles.cdp_registry_store.load_active", side_effect=_load_active):
        with TestClient(app) as client:
            fail_registry["armed"] = True
            response = client.get("/v1/project-ask/registry")
    assert response.status_code == 200
    body = response.json()
    assert body["availability"] == "unavailable"
    assert body["seat_count"] is None
    assert body["seats"] is None
