"""Shared substrate friction-file lib — cortex friction relay."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from substrate_friction_file.file import file_friction

pytestmark = pytest.mark.offline


def test_file_friction_posts_friction_dispatch() -> None:
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"item": {"id": 42}}

    with patch("substrate_friction_file.file.make_sync_client") as client_factory:
        client = client_factory.return_value.__enter__.return_value
        client.post.return_value = response
        result = file_friction(owner="service:mcp-server", note="enum lagged")

    assert result["item"]["id"] == 42
    payload = client.post.call_args.kwargs["json"]
    assert payload["tool"] == "friction"
    args = json.loads(payload["arguments"])
    assert args["owner"] == "service:mcp-server"
    assert args["note"] == "enum lagged"


def test_file_friction_surfaces_http_errors() -> None:
    response = MagicMock()
    response.status_code = 404
    response.text = "not found"

    with patch("substrate_friction_file.file.make_sync_client") as client_factory:
        client = client_factory.return_value.__enter__.return_value
        client.post.return_value = response
        result = file_friction(owner="service:missing", note="x")

    assert result["status_code"] == 404
    assert "error" in result
