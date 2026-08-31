"""Hermetic tests for a:31534 overlay picker retry."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from cdp_ask.overlay_effort_retry import (
    FABLE_FALLBACK_MODEL,
    is_opus_high_request,
    run_with_overlay_retry,
)


@pytest.mark.offline
def test_opus_high_request_includes_sealed_default() -> None:
    assert is_opus_high_request("opus-5") is True
    assert is_opus_high_request("cdp/opus-5") is True
    assert is_opus_high_request("opus-5-high") is True
    assert is_opus_high_request("opus-5-max") is False
    assert is_opus_high_request("fable-5") is False


@pytest.mark.offline
@pytest.mark.asyncio
async def test_first_model_select_fail_retries_opus_then_succeeds() -> None:
    run_once = AsyncMock(
        side_effect=[
            {"ok": False, "error": "model select failed: picker"},
            {"ok": True, "error": None},
        ]
    )
    result, extras = await run_with_overlay_retry(
        requested_model="opus-5",
        run_once=run_once,
        error_of=lambda r: r.get("error"),
        ok_of=lambda r: bool(r.get("ok")),
    )
    assert result["ok"] is True
    assert extras["family_substituted"] is False
    assert extras["overlay_retry"] == "opus_high"
    assert run_once.await_count == 2


@pytest.mark.offline
@pytest.mark.asyncio
async def test_two_opus_fails_then_fable_fallback() -> None:
    run_once = AsyncMock(
        side_effect=[
            {"ok": False, "error": "model select failed: a"},
            {"ok": False, "error": "model select failed: b"},
            {"ok": True, "error": None, "model": FABLE_FALLBACK_MODEL},
        ]
    )
    result, extras = await run_with_overlay_retry(
        requested_model="cdp/opus-5",
        run_once=run_once,
        error_of=lambda r: r.get("error"),
        ok_of=lambda r: bool(r.get("ok")),
    )
    assert result["ok"] is True
    assert extras["family_substituted"] is True
    assert extras["overlay_retry"] == "fable"
    assert extras["resolved_model"] == FABLE_FALLBACK_MODEL
    assert run_once.await_args_list[-1].args == (FABLE_FALLBACK_MODEL,)


@pytest.mark.offline
@pytest.mark.asyncio
async def test_non_picker_error_does_not_retry() -> None:
    run_once = AsyncMock(
        return_value={"ok": False, "error": "wait_assistant_reply timed out"}
    )
    result, extras = await run_with_overlay_retry(
        requested_model="opus-5",
        run_once=run_once,
        error_of=lambda r: r.get("error"),
        ok_of=lambda r: bool(r.get("ok")),
    )
    assert result["error"] == "wait_assistant_reply timed out"
    assert extras["family_substituted"] is False
    assert run_once.await_count == 1
