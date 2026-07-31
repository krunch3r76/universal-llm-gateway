"""Unit tests for email_export GraphClient reply-draft creation."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from email_export.graph_client import (
    AzureCredentials,
    GraphAuthError,
    GraphClient,
    GraphNotFoundError,
)
from email_export.intent import Selector


@pytest.fixture
def client() -> GraphClient:
    creds = AzureCredentials(
        tenant_id="tenant",
        client_id="client",
        client_secret="secret",
    )
    graph_client = GraphClient(creds)
    graph_client._token = "fake-token"
    return graph_client


def _http_client(*responses: MagicMock) -> MagicMock:
    mock = MagicMock()
    mock.__enter__.return_value = mock
    mock.__exit__.return_value = False
    if len(responses) == 1:
        mock.post.return_value = responses[0]
    elif len(responses) == 2:
        mock.post.return_value = responses[0]
        mock.patch.return_value = responses[1]
    return mock


@patch.object(GraphClient, "resolve_graph_id", return_value="source-graph-id")
@patch.object(GraphClient, "_client")
@patch.object(GraphClient, "_headers", return_value={"Authorization": "Bearer x"})
def test_create_reply_draft_two_call_sequence_preserves_quote(
    _mock_headers: MagicMock,
    mock_client_factory: MagicMock,
    _mock_resolve: MagicMock,
    client: GraphClient,
) -> None:
    create_resp = MagicMock()
    create_resp.status_code = 201
    create_resp.json.return_value = {
        "id": "draft-id-123",
        "body": {
            "contentType": "html",
            "content": "<div>quoted original</div>",
        },
    }
    patch_resp = MagicMock()
    patch_resp.status_code = 200
    mock_client_factory.return_value = _http_client(create_resp, patch_resp)

    draft_id = client.create_reply_draft(
        "user@example.com",
        Selector(kind="message_id", value="<msg@example.com>"),
        body="Dear Mr. Bowden,\n\nHello.",
    )

    assert draft_id == "draft-id-123"
    http = mock_client_factory.return_value
    http.post.assert_called_once()
    assert "/createReply" in http.post.call_args.args[0]
    http.patch.assert_called_once()
    patch_json = http.patch.call_args.kwargs["json"]
    patched = patch_json["body"]["content"]
    assert "Dear Mr. Bowden" in patched
    assert "quoted original" in patched


@patch.object(GraphClient, "resolve_graph_id", return_value="source-graph-id")
@patch.object(GraphClient, "_client")
@patch.object(GraphClient, "_headers", return_value={"Authorization": "Bearer x"})
def test_create_reply_draft_not_found_on_create_reply(
    _mock_headers: MagicMock,
    mock_client_factory: MagicMock,
    _mock_resolve: MagicMock,
    client: GraphClient,
) -> None:
    create_resp = MagicMock()
    create_resp.status_code = 404
    create_resp.text = "not found"
    mock_client_factory.return_value = _http_client(create_resp)

    with pytest.raises(GraphNotFoundError, match="createReply"):
        client.create_reply_draft(
            "user@example.com",
            Selector(kind="message_id", value="<missing@example.com>"),
            body="Body text",
        )


@patch.object(GraphClient, "resolve_graph_id", return_value="source-graph-id")
@patch.object(GraphClient, "_client")
@patch.object(GraphClient, "_headers", return_value={"Authorization": "Bearer x"})
def test_create_reply_draft_access_denied_on_create_reply(
    _mock_headers: MagicMock,
    mock_client_factory: MagicMock,
    _mock_resolve: MagicMock,
    client: GraphClient,
) -> None:
    create_resp = MagicMock()
    create_resp.status_code = 403
    create_resp.text = (
        '{"error":{"code":"ErrorAccessDenied",'
        '"message":"Access is denied. Check credentials and try again."}}'
    )
    mock_client_factory.return_value = _http_client(create_resp)

    with pytest.raises(GraphAuthError, match="createReply failed: 403"):
        client.create_reply_draft(
            "kaywan@askapharmd.me",
            Selector(kind="message_id", value="<msg@example.com>"),
            body="Body text",
        )
