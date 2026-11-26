"""Unit tests for ide.transcript CONTEXT_BUDGET thresholds."""

from __future__ import annotations

import importlib
import sys
from unittest.mock import patch

import pytest

from bus_watch import digest_budget as digest_budget_mod
from bus_watch.digest_budget import (
    build_budget_block,
    latch_used_percent_at_first_summarized,
    reset_used_percent_at_first_summarized_latch,
    seat_budget,
)

pytestmark = pytest.mark.offline


def _ide_block(
    *,
    used_tokens: int,
    tool_calls: int = 0,
    tool_result_tokens: int | None = None,
) -> dict:
    return build_budget_block(
        used_tokens=used_tokens,
        window_limit_tokens=256_000,
        model="ide-tab",
        source="ide.transcript",
        scope="liaison_seat",
        epoch="tid",
        as_of="2026-10-18T00:00:00Z",
        holder_basis="seat_lock",
        tool_calls=tool_calls,
        tool_result_tokens=tool_result_tokens,
    )


def test_ide_transcript_ratio_055_raises_context_budget() -> None:
    budget = _ide_block(used_tokens=145_000)
    assert budget["stop_class"] == "CONTEXT_BUDGET"


def test_ide_transcript_tool_calls_35_raises_context_budget() -> None:
    budget = _ide_block(used_tokens=10_000, tool_calls=35)
    assert budget["stop_class"] == "CONTEXT_BUDGET"


def test_ide_transcript_below_thresholds_no_stop() -> None:
    budget = _ide_block(used_tokens=10_000, tool_calls=10)
    assert budget["stop_class"] is None


def test_giw_sdk_stream_keeps_080_ratio() -> None:
    budget = build_budget_block(
        used_tokens=560_000,
        window_limit_tokens=700_000,
        model="cursor/claude-fable-5-1",
        source="giw.sdk_stream",
        scope="liaison_seat",
        epoch="dispatch-1",
        as_of="2026-10-18T00:00:00Z",
    )
    assert budget["stop_class"] == "CONTEXT_BUDGET"
    low = build_budget_block(
        used_tokens=500_000,
        window_limit_tokens=700_000,
        model="cursor/claude-fable-5-1",
        source="giw.sdk_stream",
        scope="liaison_seat",
        epoch="dispatch-1",
        as_of="2026-10-18T00:00:00Z",
    )
    assert low["stop_class"] is None


def test_seat_budget_tolerates_raw_glass_probe_keys() -> None:
    raw_glass = {"contextTokensUsed": 42_000, "contextTokenLimit": 200_000}
    lock = {"holder": "ide:f19ca60c-27d7-43b0-bf7e-4e7d29a9eb03"}
    with patch(
        "bus_watch.liaison_digest._measure_ide_tab", return_value=raw_glass
    ):
        budget, pct = seat_budget(
            est=0,
            successor_model="ide-tab",
            usage_live=None,
            holder_dispatch="",
            register="attended",
            lock=lock,
            policy={"ide_window_tokens": 256_000},
            root_id="10479",
            digest_ts="2026-10-21T16:44:58Z",
            epoch="",
        )
    assert budget["used_tokens"] == 42_000
    assert budget["window_limit_tokens"] == 200_000
    assert pct == 21.0


def _seat(**overrides: object) -> tuple[dict, float]:
    args: dict = {
        "est": 100,
        "successor_model": "cursor/grok-4.7",
        "usage_live": None,
        "holder_dispatch": "",
        "register": "attended",
        "lock": {},
        "policy": {},
        "root_id": "16416",
        "digest_ts": "2026-10-21T15:42:00Z",
        "epoch": "epoch-1",
    }
    args.update(overrides)
    return seat_budget(**args)


def test_seat_budget_missing_used_tokens_is_incomplete_probe() -> None:
    probe = {
        "ok": True,
        "contextTokensUsed": 42_000,
        "contextTokenLimit": 200_000,
        "transcript_id": "tab-1",
    }
    with patch("bus_watch.liaison_digest._measure_ide_tab", return_value=probe):
        budget, pct = _seat()
    assert budget["budget_probe"] == "incomplete"
    assert budget["used_tokens"] is None
    assert budget["stop_class"] is None
    assert budget["source"] == "ide.transcript"
    assert pct == 0.0
    assert 42_000 not in budget.values()


def test_tool_result_tokens_replace_tool_call_count() -> None:
    budget = _ide_block(used_tokens=10_000, tool_calls=40, tool_result_tokens=5_000)
    assert budget["stop_class"] is None
    assert budget["hop_weight_basis"] == "tool_result_tokens"


def test_tool_result_tokens_over_threshold_raises() -> None:
    budget = _ide_block(used_tokens=10_000, tool_calls=8, tool_result_tokens=60_000)
    assert budget["stop_class"] == "CONTEXT_BUDGET"
    assert budget["hop_weight_basis"] == "tool_result_tokens"


def test_tool_calls_backstop_105_raises_with_weight() -> None:
    budget = _ide_block(used_tokens=10_000, tool_calls=105, tool_result_tokens=1_000)
    assert budget["stop_class"] == "CONTEXT_BUDGET"
    assert budget["hop_weight_basis"] == "tool_result_tokens"


@pytest.mark.parametrize("bad_value", ["nope", "0", "-5"])
def test_tool_result_threshold_env_invalid_falls_back(
    monkeypatch: pytest.MonkeyPatch, bad_value: str
) -> None:
    monkeypatch.setenv("ULG_IDE_HOP_TOOL_RESULT_TOKENS", bad_value)
    reloaded = importlib.reload(digest_budget_mod)
    assert reloaded._IDE_TOOL_RESULT_TOKENS_THRESHOLD == 52_500
    monkeypatch.delenv("ULG_IDE_HOP_TOOL_RESULT_TOKENS", raising=False)
    importlib.reload(sys.modules["bus_watch.digest_budget"])


def test_seat_budget_present_used_tokens_keeps_measured_count() -> None:
    probe = {
        "used_tokens": 1200,
        "window_limit_tokens": 256_000,
        "transcript_id": "tab-1",
        "holder_basis": "seat_lock",
        "tool_calls": 1,
        "bytes": 100,
        "tokens_per_tool_call": 1500,
    }
    with patch("bus_watch.liaison_digest._measure_ide_tab", return_value=probe):
        budget, pct = _seat()
    assert budget["used_tokens"] == 1200
    assert budget["budget_probe"] is None
    assert pct == round(100.0 * 1200 / 256_000, 1)


_TID_A = "f19ca60c-27d7-43b0-bf7e-4e7d29a9eb03"
_TID_B = "06e4b108-1ca1-4593-a172-37b0a40f39e4"


def test_used_percent_at_first_summarized_latches_on_repeated_sample() -> None:
    reset_used_percent_at_first_summarized_latch()
    sub_id = 99
    high_used = build_budget_block(
        used_tokens=145_000,
        window_limit_tokens=256_000,
        model="ide-tab",
        source="ide.transcript",
        scope="liaison_seat",
        epoch=_TID_A,
        as_of="2026-11-26T00:00:00Z",
        holder_basis="endpoint_subscription",
        transcript_id=_TID_A,
        tool_calls=2,
        glass_summarized_tokens=100,
    )
    first = latch_used_percent_at_first_summarized(
        subscription_id=sub_id,
        transcript_id=_TID_A,
        glass_summarized_tokens=100,
        computed_percent=high_used["used_percent_at_first_summarized"],
    )
    low_used = build_budget_block(
        used_tokens=80_000,
        window_limit_tokens=256_000,
        model="ide-tab",
        source="ide.transcript",
        scope="liaison_seat",
        epoch=_TID_A,
        as_of="2026-11-26T00:01:00Z",
        holder_basis="endpoint_subscription",
        transcript_id=_TID_A,
        tool_calls=2,
        glass_summarized_tokens=500,
    )
    assert low_used["used_percent_at_first_summarized"] == pytest.approx(
        100.0 * 80_000 / 256_000, rel=0.01
    )
    second = latch_used_percent_at_first_summarized(
        subscription_id=sub_id,
        transcript_id=_TID_A,
        glass_summarized_tokens=500,
        computed_percent=low_used["used_percent_at_first_summarized"],
    )
    assert first == pytest.approx(56.64, rel=0.01)
    assert second == first


def test_endpoint_subscription_holder_basis_emits_pressure_band() -> None:
    budget = build_budget_block(
        used_tokens=145_000,
        window_limit_tokens=256_000,
        model="ide-tab",
        source="ide.transcript",
        scope="liaison_seat",
        epoch=_TID_A,
        as_of="2026-11-26T00:00:00Z",
        holder_basis="endpoint_subscription",
        transcript_id=_TID_A,
        tool_calls=2,
        glass_summarized_tokens=1,
    )
    assert budget["pressure_band"] == "hop"
    assert budget["used_percent_at_first_summarized"] == pytest.approx(56.64, rel=0.01)
    assert budget["stop_class"] is None


def test_endpoint_subscription_does_not_widen_stop_class() -> None:
    budget = build_budget_block(
        used_tokens=200_000,
        window_limit_tokens=256_000,
        model="ide-tab",
        source="ide.transcript",
        scope="liaison_seat",
        epoch=_TID_A,
        as_of="2026-11-26T00:00:00Z",
        holder_basis="endpoint_subscription",
        transcript_id=_TID_A,
        tool_calls=200,
        glass_summarized_tokens=500,
    )
    assert budget["stop_class"] is None
    assert budget["pressure_band"] == "emergency"


def test_prepare_band_advisory_headroom_and_read_cap() -> None:
    budget = build_budget_block(
        used_tokens=110_000,
        window_limit_tokens=256_000,
        model="ide-tab",
        source="ide.transcript",
        scope="liaison_seat",
        epoch=_TID_A,
        as_of="2026-11-26T00:00:00Z",
        holder_basis="endpoint_subscription",
        transcript_id=_TID_A,
        tool_calls=1,
    )
    assert budget["pressure_band"] == "prepare"
    adv = budget["prepare_advisory"]
    assert adv["headroom_tokens"] == 146_000
    assert adv["read_cap_tokens"] == 36_500
    assert "Does not prevent" in adv["note"]


def test_seat_lock_budget_block_unchanged_regression() -> None:
    baseline = build_budget_block(
        used_tokens=145_000,
        window_limit_tokens=256_000,
        model="ide-tab",
        source="ide.transcript",
        scope="liaison_seat",
        epoch=_TID_A,
        as_of="2026-10-18T00:00:00Z",
        holder_basis="seat_lock",
        tool_calls=10,
    )
    assert baseline["stop_class"] == "CONTEXT_BUDGET"
    assert "pressure_band" not in baseline
    assert baseline["hop_weight_basis"] == "tool_calls"
