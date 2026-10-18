"""Shared substrate friction-file lib — cortex friction relay."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import httpx
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
    assert args["category"] == "tool_error"
    assert "actionable" not in args
    assert "via_adapter" not in payload
    assert client.post.call_args.kwargs["headers"]["X-ULG-Caller"] == "substrate_friction_file"


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


def test_file_friction_protocol_without_anchor_is_not_actionable() -> None:
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"item": {"id": 7}}

    with patch("substrate_friction_file.file.make_sync_client") as client_factory:
        client = client_factory.return_value.__enter__.return_value
        client.post.return_value = response
        file_friction(
            owner="service:mcp-server",
            note="harness rejected protocol",
            category="protocol",
        )

    args = json.loads(client.post.call_args.kwargs["json"]["arguments"])
    assert args["category"] == "protocol"
    assert args["actionable"] is False
    assert args["actionable_false_reason"] == (
        "filed via unanchored substrate_friction_file"
    )


def test_file_friction_protocol_explicit_actionable_is_forwarded() -> None:
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"item": {"id": 8}}

    with patch("substrate_friction_file.file.make_sync_client") as client_factory:
        client = client_factory.return_value.__enter__.return_value
        client.post.return_value = response
        file_friction(
            owner="service:mcp-server",
            note="anchored elsewhere",
            category="protocol",
            actionable=True,
        )

    args = json.loads(client.post.call_args.kwargs["json"]["arguments"])
    assert args["actionable"] is True
    assert "actionable_false_reason" not in args


def test_file_friction_validation_body_without_status_gets_422() -> None:
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"error": "protocol friction requires an anchor"}

    with patch("substrate_friction_file.file.make_sync_client") as client_factory:
        client = client_factory.return_value.__enter__.return_value
        client.post.return_value = response
        result = file_friction(owner="service:mcp-server", note="x", category="tool_error")

    assert result["status_code"] == 422
    assert result["error"] == "protocol friction requires an anchor"


def test_file_friction_connection_error_keeps_null_status() -> None:
    with patch("substrate_friction_file.file.make_sync_client") as client_factory:
        client = client_factory.return_value.__enter__.return_value
        client.post.side_effect = httpx.ConnectError("down")
        result = file_friction(owner="service:mcp-server", note="x")

    assert result["status_code"] is None
    assert "connection failed" in result["error"]
