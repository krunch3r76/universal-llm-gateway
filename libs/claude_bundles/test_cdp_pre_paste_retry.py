"""Pre-paste CDP submit retry (a:43814)."""

from __future__ import annotations

from pathlib import Path
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


def _ctx(**overrides: object) -> PrePasteRetryContext:
    base = dict(
        thread_id="1",
        execution_id="e" * 32,
        request_id="r",
        read_chat_url=lambda: None,
        read_attempt_count=lambda: 0,
        read_satellite_execution_id=lambda: None,
        record_attempt=lambda _n: None,
        emit_attempt=lambda _n, _e: None,
        emit_exhausted=lambda _e: None,
        reattach_house=lambda: {"ok": True},
    )
    base.update(overrides)
    return PrePasteRetryContext(**base)  # type: ignore[arg-type]


def test_backoff_schedule() -> None:
    assert PRE_PASTE_BACKOFF_S == (10.0, 30.0)
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


def test_read_timeout_not_pre_paste() -> None:
    assert not is_pre_paste_failure(
        "cdp-ask unreachable: read timeout waiting for body"
    )
    assert not is_pre_paste_failure("POST timed out after 30s")


def test_post_paste_error_not_pre_paste() -> None:
    assert not is_pre_paste_failure("skill_receipt_unverified: x")


def test_pre_paste_retry_three_backoffs() -> None:
    relay = MagicMock()
    relay.submit.side_effect = CdpAskClientError("connection refused")
    sleeps: list[float] = []
    attempts: list[int] = []

    ctx = _ctx(record_attempt=attempts.append)
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
    url_after_sleep = {"set": False}

    def _sleep(_s: float) -> None:
        url_after_sleep["set"] = True

    def _url() -> str | None:
        if url_after_sleep["set"]:
            return "https://claude.ai/cowork/cse_mid"
        return None

    ctx = _ctx(read_chat_url=_url)
    with pytest.raises(CdpAskClientError):
        submit_with_pre_paste_retry(
            relay,
            {},
            client=None,
            sleep=_sleep,
            ctx=ctx,
        )
    assert relay.submit.call_count == 1


def test_dedupe_respects_prior_attempt_count() -> None:
    relay = MagicMock()
    relay.submit.side_effect = CdpAskClientError("connection refused")
    ctx = _ctx(read_attempt_count=lambda: 2)
    with pytest.raises(CdpAskClientError):
        submit_with_pre_paste_retry(
            relay,
            {},
            client=None,
            sleep=lambda _s: None,
            ctx=ctx,
        )
    assert relay.submit.call_count == 1


def test_prior_three_emits_exhausted_without_submit() -> None:
    relay = MagicMock()
    exhausted: list[str] = []
    ctx = _ctx(read_attempt_count=lambda: 3, emit_exhausted=exhausted.append)
    with pytest.raises(CdpAskClientError, match="pre_paste_retry_exhausted"):
        submit_with_pre_paste_retry(
            relay,
            {},
            client=None,
            sleep=lambda _s: None,
            ctx=ctx,
        )
    assert relay.submit.call_count == 0
    assert exhausted == ["pre_paste_retry_exhausted"]


def test_reattach_invoked_on_retry_path() -> None:
    relay = MagicMock()
    relay.submit.side_effect = [
        CdpAskClientError("connection refused"),
        {"execution_id": "sat" * 16},
    ]
    reattach_calls: list[dict[str, object]] = []

    def _reattach() -> dict[str, object]:
        reattach_calls.append({"ok": True, "reattached": True})
        return reattach_calls[-1]

    ctx = _ctx(reattach_house=_reattach)
    out = submit_with_pre_paste_retry(
        relay,
        {},
        client=None,
        sleep=lambda _s: None,
        ctx=ctx,
    )
    assert out["execution_id"] == "sat" * 16
    assert len(reattach_calls) == 1


def test_upsert_preserves_pre_paste_attempt_count(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from systems.frontier_consult.cdp_generate_inflight_ledger import (
        read_pre_paste_retry_attempts,
        record_pre_paste_retry_attempt,
        upsert_inflight_leg,
    )

    eid = "f" * 32
    upsert_inflight_leg(
        execution_id=eid,
        request_id="r",
        thread_id="1",
        pointer_turn=1,
        caller_agent="test",
        prompt_uri="cortex://x",
        model_id="opus",
        max_wall_s=60.0,
    )
    record_pre_paste_retry_attempt(execution_id=eid, attempt=2)
    upsert_inflight_leg(
        execution_id=eid,
        request_id="r",
        thread_id="1",
        pointer_turn=1,
        caller_agent="test",
        prompt_uri="cortex://x",
        model_id="opus",
        max_wall_s=60.0,
    )
    assert read_pre_paste_retry_attempts(execution_id=eid) == 2


def test_file_proof() -> None:
    import pathlib

    here = pathlib.Path(__file__).resolve()
    assert here.name == "test_cdp_pre_paste_retry.py"
