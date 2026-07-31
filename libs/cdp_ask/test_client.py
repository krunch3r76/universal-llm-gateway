"""Unit tests for ``cdp_ask.client`` (native CDP HTTP client)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from cdp_ask.client import CdpAskClient, CdpAskClientError
from cdp_ask.models import SubmitProjectAskRequest


def test_submit_builds_project_ask_path() -> None:
    client = CdpAskClient(base_url="http://satellite:8770")
    mock_http = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 202
    mock_resp.content = b'{"execution_id":"e1","status":"pending"}'
    mock_resp.json.return_value = {"execution_id": "e1", "status": "pending"}
    mock_http.request.return_value = mock_resp

    out = client.submit(
        SubmitProjectAskRequest(
            prompt_uri="cortex://notes/x.md",
            model="opus-5",
            harvest_source="output-file",
            expected_size="large",
            download_output=True,
        ),
        client=mock_http,
    )
    assert out["execution_id"] == "e1"
    args, kwargs = mock_http.request.call_args
    assert args[0] == "POST"
    assert args[1] == "/v1/project-ask/executions"
    body = kwargs["json"]
    assert body["harvest_source"] == "output-file"
    assert body["expected_size"] == "large"
    assert body["download_output"] is True


def test_missing_base_url_raises() -> None:
    client = CdpAskClient(base_url="")
    with pytest.raises(CdpAskClientError, match="PROJECT_ASK_URL"):
        client.poll("e1")
