from __future__ import annotations

from decimal import Decimal

import pytest

from research_dialogue._types import SendResult
from research_dialogue.session import TURN_LIMIT, evaluate_stop_rules

pytestmark = pytest.mark.offline


def test_turn_limit_stop() -> None:
    state = {"turn_count": TURN_LIMIT}
    stop = evaluate_stop_rules(
        state,
        pre_reason=None,
        send_result=SendResult(True, 200, "x", {}, "end_turn", "m"),
        parsed_ok=True,
        usage={},
        converged=False,
    )
    assert stop == "turn_limit"


def test_stall_stop() -> None:
    state = {"turn_count": 2, "stall_streak": 2}
    stop = evaluate_stop_rules(
        state,
        pre_reason=None,
        send_result=SendResult(True, 200, "x", {}, "end_turn", "m"),
        parsed_ok=True,
        usage={},
        converged=False,
    )
    assert stop == "stall"


def test_smoke_pass(tmp_path) -> None:
    from datetime import UTC, datetime

    from research_dialogue._types import Snapshot
    from research_dialogue.cost import billed_usd
    from research_dialogue.session import run_smoke

    usage = {
        "input_tokens": 100,
        "output_tokens": 10,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
    }
    billed = billed_usd(usage, model="claude-sonnet-5")
    sb = Snapshot(
        as_of_utc=datetime(2026, 1, 1, tzinfo=UTC),
        healthy=True,
        committed_usd=Decimal("0"),
        open_reservations_usd=Decimal("0"),
        window_start="w",
        ledger_instance_id="i",
        latch_active=False,
    )
    sa = Snapshot(
        as_of_utc=datetime(2026, 1, 1, 1, tzinfo=UTC),
        healthy=True,
        committed_usd=billed,
        open_reservations_usd=Decimal("0"),
        window_start="w",
        ledger_instance_id="i",
        latch_active=False,
        last_entries=[
            {
                "kind": "commit",
                "model": "claude-sonnet-5",
                "usd": str(billed),
                "created_at": "2026-01-01T00:30:00+00:00",
            }
        ],
    )
    seq = [sb, sa]

    def read_fn() -> Snapshot:
        return seq.pop(0)

    def send_fn(body: dict) -> SendResult:
        return SendResult(True, 200, "OK", usage, "end_turn", "m")

    assert run_smoke(tmp_path, read_fn=read_fn, send_fn=send_fn) == 0


def test_smoke_fail_underbilled(tmp_path) -> None:
    from datetime import UTC, datetime

    from research_dialogue._types import Snapshot
    from research_dialogue.session import run_smoke

    usage = {
        "input_tokens": 100,
        "output_tokens": 10,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
    }
    sb = Snapshot(
        as_of_utc=datetime(2026, 1, 1, tzinfo=UTC),
        healthy=True,
        committed_usd=Decimal("0"),
        open_reservations_usd=Decimal("0"),
        window_start="w",
        ledger_instance_id="i",
        latch_active=False,
    )
    sa = Snapshot(
        as_of_utc=datetime(2026, 1, 1, 1, tzinfo=UTC),
        healthy=True,
        committed_usd=Decimal("0.000001"),
        open_reservations_usd=Decimal("0"),
        window_start="w",
        ledger_instance_id="i",
        latch_active=False,
    )
    seq = [sb, sa]

    def read_fn() -> Snapshot:
        return seq.pop(0)

    def send_fn(body: dict) -> SendResult:
        return SendResult(True, 200, "OK", usage, "end_turn", "m")

    assert run_smoke(tmp_path, read_fn=read_fn, send_fn=send_fn) == 2
