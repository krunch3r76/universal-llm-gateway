from __future__ import annotations

from unittest.mock import MagicMock, patch

from tools._cortex_relay import (
    _REQUEST_TIMEOUT,
    cx,
    resolve_cortex_timeout,
)


def test_default_cortex_relay_timeout() -> None:
    assert resolve_cortex_timeout("GET", "/entities/todo:foo") == _REQUEST_TIMEOUT


def test_todo_distill_implement_gate_uses_extended_timeout() -> None:
    assert (
        resolve_cortex_timeout(
            "POST",
            "/dispatch",
            dispatch_tool="todo_distill_implement_gate",
        )
        == 90.0
    )


def test_other_dispatch_ops_keep_default_timeout() -> None:
    assert (
        resolve_cortex_timeout("POST", "/dispatch", dispatch_tool="assert") == 30.0
    )


def test_cx_passes_extended_timeout_to_http_client() -> None:
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"ok": True}

    mock_client = MagicMock()
    mock_client.request.return_value = mock_response
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    with patch(
        "tools._cortex_relay.make_sync_client", return_value=mock_client
    ) as make_client:
        result = cx(
            "POST",
            "/dispatch",
            {"tool": "todo_distill_implement_gate", "arguments": "{}"},
            dispatch_tool="todo_distill_implement_gate",
        )

    assert result == {"ok": True}
    make_client.assert_called_once()
    assert make_client.call_args.kwargs["timeout"] == 90.0


def test_cx_default_dispatch_tool_uses_30s() -> None:
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"ok": True}

    mock_client = MagicMock()
    mock_client.request.return_value = mock_response
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    with patch(
        "tools._cortex_relay.make_sync_client", return_value=mock_client
    ) as make_client:
        cx("GET", "/entities/todo:foo")

    assert make_client.call_args.kwargs["timeout"] == 30.0


def test_cx_records_dispatch_tool_on_called_event() -> None:
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"ok": True}

    mock_client = MagicMock()
    mock_client.request.return_value = mock_response
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    with (
        patch("tools._cortex_relay.make_sync_client", return_value=mock_client),
        patch("tools._cortex_relay.record") as record,
    ):
        cx(
            "POST",
            "/dispatch",
            {"tool": "todo_distill_implement_gate", "arguments": "{}"},
            dispatch_tool="todo_distill_implement_gate",
        )

    called_kwargs = record.call_args_list[0].kwargs
    assert called_kwargs["timeout_s"] == 90.0
    assert called_kwargs["dispatch_tool"] == "todo_distill_implement_gate"
