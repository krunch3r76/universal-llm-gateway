"""Unit tests for ide.transcript CONTEXT_BUDGET thresholds."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from bus_watch.digest_budget import build_budget_block, seat_budget

pytestmark = pytest.mark.offline


def _ide_block(*, used_tokens: int, tool_calls: int = 0) -> dict:
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
