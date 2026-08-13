"""Shared substrate graph write lib — cortex assert relay."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from substrate_graph_write.write import write_claim

pytestmark = pytest.mark.offline


def test_write_claim_posts_assert_dispatch() -> None:
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"item": {"id": 42}}

    with patch("substrate_graph_write.write.make_sync_client") as client_factory:
        client = client_factory.return_value.__enter__.return_value
        client.post.return_value = response
        result = write_claim(entity_id="todo:foo", claim="rot observed")

    assert result["item"]["id"] == 42
    payload = client.post.call_args.kwargs["json"]
    assert payload["tool"] == "assert"
    args = json.loads(payload["arguments"])
    assert args["entity_id"] == "todo:foo"
    assert args["claim"] == "rot observed"
    assert args["confidence"] == "confirmed"
    assert args["derivation_type"] == "direct_observation"
    assert args["evidence"] == "substrate_graph_write"


def test_write_claim_surfaces_http_errors() -> None:
    response = MagicMock()
    response.status_code = 404
    response.text = "not found"

    with patch("substrate_graph_write.write.make_sync_client") as client_factory:
        client = client_factory.return_value.__enter__.return_value
        client.post.return_value = response
        result = write_claim(entity_id="todo:missing", claim="x")

    assert result["status_code"] == 404
    assert "error" in result
