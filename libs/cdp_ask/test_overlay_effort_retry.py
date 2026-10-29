"""Hermetic tests for a:31534 overlay picker retry (friction:40692)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from cdp_ask.overlay_effort_retry import (
    is_opus_high_request,
    run_with_overlay_retry,
    substitute_opus_55_at_request_effort,
)


@pytest.mark.offline
def test_opus_high_request_includes_sealed_default() -> None:
    assert is_opus_high_request("opus-5") is True
    assert is_opus_high_request("cdp/opus-5") is True
    assert is_opus_high_request("opus-5-high") is True
    assert is_opus_high_request("opus-5-max") is False
    assert is_opus_high_request("fable-5") is False


@pytest.mark.offline
def test_substitute_opus_55_preserves_effort() -> None:
    assert substitute_opus_55_at_request_effort("cdp/opus-5") == "cdp/opus-5.5-high"
    assert substitute_opus_55_at_request_effort("opus-5-high") == "opus-5.5-high"


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
    assert extras["model_substituted"] is False
    assert extras["overlay_retry"] == "opus_high"
    assert run_once.await_count == 2


@pytest.mark.offline
@pytest.mark.asyncio
async def test_opus_request_never_attempts_fable() -> None:
    run_once = AsyncMock(
        side_effect=[
            {"ok": False, "error": "model select failed: a"},
            {"ok": False, "error": "model select failed: b"},
            {"ok": False, "error": "model select failed: c"},
        ]
    )
    _result, extras = await run_with_overlay_retry(
        requested_model="cdp/opus-5",
        run_once=run_once,
        error_of=lambda r: r.get("error"),
        ok_of=lambda r: bool(r.get("ok")),
    )
    assert run_once.await_count == 3
    assert not any("fable" in str(m).lower() for m in extras["overlay_select_attempts"])


@pytest.mark.offline
@pytest.mark.asyncio
async def test_two_opus_fails_then_opus_55_substitution_recorded() -> None:
    resolved = "cdp/opus-5.5-high"
    run_once = AsyncMock(
        side_effect=[
            {"ok": False, "error": "model select failed: a"},
            {"ok": False, "error": "model select failed: b"},
            {"ok": True, "error": None, "model": resolved},
        ]
    )
    result, extras = await run_with_overlay_retry(
        requested_model="cdp/opus-5",
        run_once=run_once,
        error_of=lambda r: r.get("error"),
        ok_of=lambda r: bool(r.get("ok")),
    )
    assert result["ok"] is True
    assert extras["model_substituted"] is True
    assert extras["overlay_retry"] == "opus_5_5"
    assert extras["requested_model"] == "cdp/opus-5"
    assert extras["resolved_model"] == resolved
    assert run_once.await_args_list[-1].args == (resolved,)


@pytest.mark.offline
@pytest.mark.asyncio
async def test_fable_request_untouched_on_picker_miss() -> None:
    run_once = AsyncMock(
        return_value={"ok": False, "error": "model select failed: picker"},
    )
    result, extras = await run_with_overlay_retry(
        requested_model="cdp/fable-5.1",
        run_once=run_once,
        error_of=lambda r: r.get("error"),
        ok_of=lambda r: bool(r.get("ok")),
    )
    assert result["ok"] is False
    assert extras["model_substituted"] is False
    assert run_once.await_count == 1
    assert extras["overlay_select_attempts"] == ["cdp/fable-5.1"]


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
    assert extras["model_substituted"] is False
    assert run_once.await_count == 1


@pytest.mark.offline
@pytest.mark.asyncio
async def test_code_surface_skips_opus_55_after_two_opus_fails() -> None:
    run_once = AsyncMock(
        side_effect=[
            {"ok": False, "error": "model select failed: a"},
            {"ok": False, "error": "model select failed: b"},
        ]
    )
    result, extras = await run_with_overlay_retry(
        requested_model="cdp/opus-5",
        run_once=run_once,
        error_of=lambda r: r.get("error"),
        ok_of=lambda r: bool(r.get("ok")),
        allow_family_substitution=False,
    )
    assert result["ok"] is False
    assert extras["model_substituted"] is False
    assert run_once.await_count == 2
    assert "opus-5.5" not in str(extras["overlay_select_attempts"])
