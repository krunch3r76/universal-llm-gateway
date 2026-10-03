"""MCP list filter and validate near-match relay."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from tools.pipeline import _pipeline_list, _pipeline_validate

pytestmark = pytest.mark.offline


def _ctx(response: MagicMock) -> MagicMock:
    client = MagicMock()
    client.get.return_value = response
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=False)
    return client


def test_list_passes_category_and_omits_it_when_absent() -> None:
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"members": {"demo-pipe": {"category": "demo"}}}
    with patch("tools.pipeline.make_sync_client", return_value=_ctx(response)) as made:
        listed = _pipeline_list("demo")
        bare = _pipeline_list(None)
    assert listed["members"]["demo-pipe"]["category"] == "demo"
    assert "members" in bare
    calls = made.return_value.get.call_args_list
    assert calls[0].args[0] == "/api/v1/capabilities"
    assert calls[0].kwargs["params"] == {"category": "demo"}
    assert calls[1].kwargs["params"] is None


def test_validate_relays_capped_near_matches_without_id_dump() -> None:
    catalog = [f"alpha-{index}" for index in range(8)]
    response = MagicMock()
    response.status_code = 404
    response.json.return_value = {
        "error": {
            "code": "capability_not_found",
            "message": "missing",
            "data": {"near_matches": catalog[:5]},
        }
    }
    with patch("tools.pipeline.make_sync_client", return_value=_ctx(response)):
        payload = _pipeline_validate("alpha-0x")
    matches = payload["error"]["data"]["near_matches"]
    assert matches == catalog[:5]
    assert len(matches) <= 5
    assert "alpha-7" not in matches
    assert "pipelines" not in payload
    assert "alpha-6" not in str(payload)
