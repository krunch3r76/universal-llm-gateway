"""HTTP ledger client for operator_hop_harvest (no services imports)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx

from operator_hop_harvest.ledger import fetch_latest_terminal_conductor


def test_fetch_latest_terminal_conductor_success() -> None:
    payload = {
        "found": True,
        "dispatch_id": "d-1",
        "status": "completed",
        "thread_id": "12291",
        "hop_seq": 1,
        "record_json": {},
        "closeout_body": "stop: CONSULT_PENDING",
        "scoreboard_uri": "cortex://x",
        "closeout_turn": 2,
        "consult_pending_continue_owed": False,
    }
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = payload

    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.return_value = mock_resp

    with patch("operator_hop_harvest.ledger.httpx.Client", return_value=mock_client):
        result = fetch_latest_terminal_conductor("12291")

    assert result["ledger_unreachable"] is False
    assert result["row"]["dispatch_id"] == "d-1"
    mock_client.get.assert_called_once()


def test_fetch_latest_terminal_conductor_http_error_fail_closed() -> None:
    with patch(
        "operator_hop_harvest.ledger.httpx.Client",
        side_effect=httpx.ConnectError("refused"),
    ):
        result = fetch_latest_terminal_conductor("12291")
    assert result["ledger_unreachable"] is True
    assert result["row"] is None
