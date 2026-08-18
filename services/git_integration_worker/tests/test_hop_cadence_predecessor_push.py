"""Tests for predecessor stand-down push at succession confirm (G2)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from cdp_ask.client import CdpAskClientError

from services.git_integration_worker.cursor_auto.hop_cadence_predecessor import (
    PredecessorHandle,
    PredecessorVerdict,
)
from services.git_integration_worker.cursor_auto.hop_cadence_predecessor_push import (
    push_predecessor_receipt,
)

pytestmark = pytest.mark.offline

_THREAD = "6885"
_OLD_REG = "reg-old"
_NEW_REG = "reg-new"
_EXEC = "exec-incumbent"
_SUCCESSOR_EXEC = "exec-successor"


def _handle(
    *, verdict: PredecessorVerdict = PredecessorVerdict.INCUMBENT_RECORDED
) -> PredecessorHandle:
    return PredecessorHandle(
        registration_id=_OLD_REG,
        execution_id=_EXEC,
        verdict=verdict,
    )


def test_push_skipped_for_non_incumbent_verdict() -> None:
    client = MagicMock()
    outcome = push_predecessor_receipt(
        thread_id=_THREAD,
        handle=_handle(verdict=PredecessorVerdict.FIRST_SEAT_ON_LANE),
        new_registration_id=_NEW_REG,
        matched_execution_id=_SUCCESSOR_EXEC,
        client=client,
    )
    assert outcome == {"attempted": False, "ok": False}
    client.paste.assert_not_called()


@patch(
    "services.git_integration_worker.cursor_auto.hop_cadence_predecessor_push.emit_predecessor_pushed",
)
def test_push_success(emit_pushed: MagicMock) -> None:
    client = MagicMock()
    client.paste.return_value = {"ok": True, "send_verified": True}
    outcome = push_predecessor_receipt(
        thread_id=_THREAD,
        handle=_handle(),
        new_registration_id=_NEW_REG,
        matched_execution_id=_SUCCESSOR_EXEC,
        client=client,
    )
    assert outcome == {"attempted": True, "ok": True}
    client.paste.assert_called_once()
    payload = client.paste.call_args.args[0]
    assert payload["registration_id"] == _OLD_REG
    assert payload["envelope"] == "stand_down"
    assert payload["idempotency_key"] == f"hop-cadence-stand-down:{_THREAD}:{_OLD_REG}"
    assert "TYPE: SEAT_STAND_DOWN" in payload["prompt_text"]
    emit_pushed.assert_called_once()
    assert emit_pushed.call_args.kwargs["ok"] is True


@patch(
    "services.git_integration_worker.cursor_auto.hop_cadence_predecessor_push.emit_predecessor_pushed",
)
def test_push_fail_soft_on_client_error(emit_pushed: MagicMock) -> None:
    client = MagicMock()
    client.paste.side_effect = CdpAskClientError("unreachable")
    outcome = push_predecessor_receipt(
        thread_id=_THREAD,
        handle=_handle(),
        new_registration_id=_NEW_REG,
        matched_execution_id=_SUCCESSOR_EXEC,
        client=client,
    )
    assert outcome == {"attempted": True, "ok": False}
    emit_pushed.assert_called_once()
    assert emit_pushed.call_args.kwargs["ok"] is False


@patch(
    "services.git_integration_worker.cursor_auto.hop_cadence_predecessor_push.emit_predecessor_pushed",
)
def test_push_idempotency_key_stable(emit_pushed: MagicMock) -> None:
    client = MagicMock()
    client.paste.return_value = {"ok": True}
    push_predecessor_receipt(
        thread_id=_THREAD,
        handle=_handle(),
        new_registration_id=_NEW_REG,
        matched_execution_id=_SUCCESSOR_EXEC,
        client=client,
    )
    key = client.paste.call_args.args[0]["idempotency_key"]
    client.paste.reset_mock()
    push_predecessor_receipt(
        thread_id=_THREAD,
        handle=_handle(),
        new_registration_id=_NEW_REG,
        matched_execution_id=_SUCCESSOR_EXEC,
        client=client,
    )
    assert client.paste.call_args.args[0]["idempotency_key"] == key
