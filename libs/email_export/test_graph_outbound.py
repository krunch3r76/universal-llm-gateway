"""Unit tests for Graph outbound draft create and send."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from email_export.graph_client import GraphAuthError, GraphClient, GraphNotFoundError
from email_export.graph_outbound import create_message_draft, send_draft_message


@pytest.fixture
def client() -> GraphClient:
    graph_client = GraphClient(
        __import__("email_export.graph_client", fromlist=["AzureCredentials"]).AzureCredentials(
            tenant_id="t",
            client_id="c",
            client_secret="s",
        )
    )
    graph_client._token = "tok"
    return graph_client


def _http(*, post_side: list[MagicMock] | None = None) -> MagicMock:
    mock = MagicMock()
    mock.__enter__.return_value = mock
    mock.__exit__.return_value = False
    if post_side:
        mock.post.side_effect = post_side
    return mock


@patch.object(GraphClient, "_headers", return_value={"Authorization": "Bearer x"})
@patch.object(GraphClient, "_client")
def test_create_message_draft_returns_id(
    mock_client_factory: MagicMock,
    _headers: MagicMock,
    client: GraphClient,
) -> None:
    resp = MagicMock()
    resp.status_code = 201
    resp.json.return_value = {"id": "graph-draft-abc"}
    mock_client_factory.return_value = _http(post_side=[resp])

    draft_id = create_message_draft(
        client,
        "user@example.com",
        to=["dest@example.com"],
        subject="Hello",
        body_text="Body",
    )
    assert draft_id == "graph-draft-abc"


@patch.object(GraphClient, "_headers", return_value={"Authorization": "Bearer x"})
@patch.object(GraphClient, "_client")
def test_send_draft_message_success(
    mock_client_factory: MagicMock,
    _headers: MagicMock,
    client: GraphClient,
) -> None:
    resp = MagicMock()
    resp.status_code = 202
    mock_client_factory.return_value = _http(post_side=[resp])

    payload = send_draft_message(client, "user@example.com", "graph-draft-abc")
    assert payload["status"] == "sent"
    assert payload["transport"] == "m365_graph"


@patch.object(GraphClient, "_headers", return_value={"Authorization": "Bearer x"})
@patch.object(GraphClient, "_client")
def test_send_draft_not_found(
    mock_client_factory: MagicMock,
    _headers: MagicMock,
    client: GraphClient,
) -> None:
    resp = MagicMock()
    resp.status_code = 404
    resp.text = "missing"
    mock_client_factory.return_value = _http(post_side=[resp])

    with pytest.raises(GraphNotFoundError):
        send_draft_message(client, "user@example.com", "missing-id")


@patch.object(GraphClient, "_headers", return_value={"Authorization": "Bearer x"})
@patch.object(GraphClient, "_client")
def test_create_draft_auth_denied(
    mock_client_factory: MagicMock,
    _headers: MagicMock,
    client: GraphClient,
) -> None:
    resp = MagicMock()
    resp.status_code = 403
    resp.text = "denied"
    mock_client_factory.return_value = _http(post_side=[resp])

    with pytest.raises(GraphAuthError, match="create draft failed"):
        create_message_draft(
            client,
            "kaywan@askapharmd.me",
            to=["hr@example.com"],
            subject="Test",
            body_text="Hi",
        )
