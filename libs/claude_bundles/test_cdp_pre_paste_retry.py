"""Pre-paste CDP submit retry (a:43814)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from cdp_ask.client import CdpAskClientError

from claude_bundles.cdp_pre_paste_retry import (
    MAX_PRE_PASTE_ATTEMPTS,
    PRE_PASTE_BACKOFF_S,
    PrePasteRetryContext,
    has_claude_conversation_url,
    is_post_paste_state,
    is_pre_paste_failure,
    submit_with_pre_paste_retry,
)

pytestmark = pytest.mark.offline


def test_backoff_schedule() -> None:
    assert PRE_PASTE_BACKOFF_S == (10.0, 30.0, 90.0)
    assert MAX_PRE_PASTE_ATTEMPTS == 3


def test_url_exists_no_refire_classification() -> None:
    assert has_claude_conversation_url("https://claude.ai/cowork/cse_x")
    assert is_post_paste_state(
        chat_url="https://claude.ai/cowork/cse_x",
        satellite_execution_id=None,
    )


def test_post_paste_satellite_id() -> None:
    assert is_post_paste_state(chat_url=None, satellite_execution_id="abc")


@pytest.mark.parametrize(
    "msg",
    [
        "cdp-ask unreachable: connection refused",
        "no tab open for holder",
        "CDP connect timeout",
    ],
)
def test_pre_paste_tokens(msg: str) -> None:
    assert is_pre_paste_failure(msg)


def test_post_paste_error_not_pre_paste() -> None:
    assert not is_pre_paste_failure("skill_receipt_unverified: x")


def test_pre_paste_retry_three_backoffs() -> None:
    relay = MagicMock()
    relay.submit.side_effect = CdpAskClientError("cdp-ask unreachable")
    sleeps: list[float] = []
    attempts: list[int] = []

    ctx = PrePasteRetryContext(
        thread_id="1",
        execution_id="e" * 32,
        request_id="r",
        read_chat_url=lambda: None,
        read_attempt_count=lambda: 0,
        record_attempt=attempts.append,
        emit_attempt=lambda n, _e: None,
        emit_exhausted=lambda _e: None,
        reattach_house=lambda: {"ok": True},
        probe_health=lambda: {"status": "ok"},
    )
    with pytest.raises(CdpAskClientError):
        submit_with_pre_paste_retry(
            relay,
            {"prompt_uri": "x"},
            client=None,
            sleep=sleeps.append,
            ctx=ctx,
        )
    assert relay.submit.call_count == 3
    assert sleeps == [10.0, 30.0]
    assert attempts == [1, 2, 3]


def test_url_recorded_mid_retry_stops() -> None:
    relay = MagicMock()
    relay.submit.side_effect = CdpAskClientError("connection refused")
    seen_url = {"n": 0}

    def _url() -> str | None:
        seen_url["n"] += 1
        if seen_url["n"] >= 2:
            return "https://claude.ai/cowork/cse_mid"
        return None

    ctx = PrePasteRetryContext(
        thread_id="1",
        execution_id="e" * 32,
        request_id="r",
        read_chat_url=_url,
        read_attempt_count=lambda: 0,
        record_attempt=lambda _n: None,
        emit_attempt=lambda _n, _e: None,
        emit_exhausted=lambda _e: None,
        reattach_house=lambda: {"ok": True},
        probe_health=lambda: {"ok": True},
    )
    with pytest.raises(CdpAskClientError):
        submit_with_pre_paste_retry(
            relay,
            {},
            client=None,
            sleep=lambda _s: None,
            ctx=ctx,
        )
    assert relay.submit.call_count == 1


def test_dedupe_respects_prior_attempt_count() -> None:
    relay = MagicMock()
    relay.submit.side_effect = CdpAskClientError("connection refused")
    ctx = PrePasteRetryContext(
        thread_id="1",
        execution_id="e" * 32,
        request_id="r",
        read_chat_url=lambda: None,
        read_attempt_count=lambda: 2,
        record_attempt=lambda _n: None,
        emit_attempt=lambda _n, _e: None,
        emit_exhausted=lambda _e: None,
        reattach_house=lambda: {"ok": True},
        probe_health=lambda: {"ok": True},
    )
    with pytest.raises(CdpAskClientError):
        submit_with_pre_paste_retry(
            relay,
            {},
            client=None,
            sleep=lambda _s: None,
            ctx=ctx,
        )
    assert relay.submit.call_count == 1


def test_file_proof() -> None:
    import pathlib

    here = pathlib.Path(__file__).resolve()
    assert here.name == "test_cdp_pre_paste_retry.py"
