"""Unit tests for ``stargate_chat`` transport primitives."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import httpx

from stargate_chat import call_stargate, extract_stargate_text


def _success_response(content: str = "hello") -> dict[str, Any]:
    return {
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }


def test_extract_stargate_text_success() -> None:
    assert extract_stargate_text(_success_response("extracted")) == "extracted"


def test_extract_stargate_text_error_prefix() -> None:
    assert extract_stargate_text({"error": "timeout"}) == "[OCR error: timeout]"


def test_extract_stargate_text_empty_choices() -> None:
    assert extract_stargate_text({"choices": []}) == ""


@patch("stargate_chat.make_sync_client")
def test_call_stargate_success(mock_client_factory: MagicMock) -> None:
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = _success_response("ok")
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.post.return_value = mock_resp
    mock_client_factory.return_value = mock_client

    result = call_stargate(
        "http://stargate:9999",
        [{"role": "user", "content": "hi"}],
        model="openai/gpt-5.4",
        system="sys",
        max_tokens=100,
    )

    assert result == _success_response("ok")
    mock_client.post.assert_called_once()
    body = mock_client.post.call_args.kwargs["json"]
    assert body["model"] == "openai/gpt-5.4"
    assert body["max_tokens"] == 100
    assert body["messages"][0] == {"role": "system", "content": "sys"}


@patch("stargate_chat.make_sync_client")
def test_call_stargate_timeout(mock_client_factory: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.post.side_effect = httpx.TimeoutException("timeout")
    mock_client_factory.return_value = mock_client

    result = call_stargate(
        "http://stargate:9999",
        [{"role": "user", "content": "hi"}],
        model="openai/gpt-5.4",
    )

    assert result == {"error": "Stargate timeout"}


@patch("stargate_chat.make_sync_client")
def test_call_stargate_http_error(mock_client_factory: MagicMock) -> None:
    mock_resp = MagicMock()
    mock_resp.status_code = 502
    mock_resp.text = "bad gateway"
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.post.return_value = mock_resp
    mock_client_factory.return_value = mock_client

    result = call_stargate(
        "http://stargate:9999",
        [{"role": "user", "content": "hi"}],
        model="openai/gpt-5.4",
    )

    assert result["error"] == "Upstream error (502)"


def test_importable_from_ocr_core_chain() -> None:
    import ocr_core

    assert hasattr(ocr_core, "ocr_pages")
    assert not hasattr(ocr_core, "_call_stargate")
