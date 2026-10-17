from __future__ import annotations

from decimal import Decimal

import pytest

from research_dialogue._types import SendResult
from research_dialogue.session import TURN_LIMIT, evaluate_stop_rules

pytestmark = pytest.mark.offline


def test_pair_by_request_id_ignores_bare_model_commit_row() -> None:
    from research_dialogue.session import _pair_by_request_id

    entries = [
        {
            "kind": "reserve",
            "model": "claude-opus-5-5",
            "request_id": "r1",
            "usd": "0.10",
        },
        {
            "kind": "commit",
            "model": "claude-opus-5-5",
            "request_id": "r1",
            "usd": "0.10",
        },
    ]
    pairs = _pair_by_request_id(entries, "claude-opus-5-5")
    assert pairs == {}

    prefixed = [
        {
            "kind": "reserve",
            "model": "anthropic/claude-opus-5-5",
            "request_id": "r2",
            "usd": "0.10",
        },
        {
            "kind": "commit",
            "model": "anthropic/claude-opus-5-5",
            "request_id": "r2",
            "usd": "0.10",
        },
    ]
    prefixed_pairs = _pair_by_request_id(prefixed, "claude-opus-5-5")
    assert prefixed_pairs["r2"]["commit_row"] is not None
    assert prefixed_pairs["r2"]["commit_usd"] == Decimal("0.10")


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


def _smoke_snaps(*, billed: Decimal, entries: list[dict], committed_a: Decimal | None = None):
    from datetime import UTC, datetime

    from research_dialogue._types import Snapshot

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
        committed_usd=committed_a if committed_a is not None else billed,
        open_reservations_usd=Decimal("0"),
        window_start="w",
        ledger_instance_id="i",
        latch_active=False,
        last_entries=entries,
    )
    return sb, sa


def test_smoke_exact(tmp_path) -> None:
    from research_dialogue.cost import billed_usd
    from research_dialogue.session import run_smoke

    usage = {
        "input_tokens": 100,
        "output_tokens": 10,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
    }
    billed, _, _ = billed_usd(usage, model="claude-sonnet-5")
    sb, sa = _smoke_snaps(
        billed=billed,
        entries=[
            {
                "kind": "reserve",
                "model": "anthropic/claude-sonnet-5",
                "request_id": "r1",
                "usd": str(billed),
            },
            {
                "kind": "commit",
                "model": "anthropic/claude-sonnet-5",
                "request_id": "r1",
                "usd": str(billed),
            },
        ],
    )
    seq = [sb, sa]

    def read_fn():
        return seq.pop(0)

    def send_fn(body: dict) -> SendResult:
        assert "stream" not in body
        return SendResult(True, 200, "OK", usage, "end_turn", "m")

    assert run_smoke(tmp_path, read_fn=read_fn, send_fn=send_fn) == 0
    smoke = __import__("json").loads((tmp_path / "smoke.json").read_text())
    assert smoke["smoke_result"] == "exact"


def test_smoke_floor(tmp_path) -> None:
    from research_dialogue.cost import billed_usd
    from research_dialogue.session import run_smoke

    usage = {
        "input_tokens": 100,
        "output_tokens": 10,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
    }
    billed, _, _ = billed_usd(usage, model="claude-sonnet-5")
    reserve = billed + Decimal("0.05")
    sb, sa = _smoke_snaps(
        billed=billed,
        committed_a=reserve,
        entries=[
            {
                "kind": "reserve",
                "model": "anthropic/claude-sonnet-5",
                "request_id": "r1",
                "usd": str(reserve),
            },
            {
                "kind": "commit",
                "model": "anthropic/claude-sonnet-5",
                "request_id": "r1",
                "usd": str(reserve),
            },
        ],
    )
    seq = [sb, sa]

    def read_fn():
        return seq.pop(0)

    def send_fn(body: dict) -> SendResult:
        return SendResult(True, 200, "OK", usage, "end_turn", "m")

    assert run_smoke(tmp_path, read_fn=read_fn, send_fn=send_fn) == 2
    smoke = __import__("json").loads((tmp_path / "smoke.json").read_text())
    assert smoke["smoke_result"] == "floor"


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
    smoke = __import__("json").loads((tmp_path / "smoke.json").read_text())
    assert smoke["smoke_result"] == "fail"


def test_smoke_fail_when_send_not_ok(tmp_path) -> None:
    from datetime import UTC, datetime

    from research_dialogue._types import Snapshot
    from research_dialogue.session import run_smoke

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
        committed_usd=Decimal("0"),
        open_reservations_usd=Decimal("0"),
        window_start="w",
        ledger_instance_id="i",
        latch_active=False,
    )
    seq = [sb, sa]

    def read_fn() -> Snapshot:
        return seq.pop(0)

    def send_fn(body: dict) -> SendResult:
        return SendResult(
            ok=False,
            status_code=503,
            text="",
            usage={},
            stop_reason=None,
            message_id=None,
            error_body="upstream down",
        )

    assert run_smoke(tmp_path, read_fn=read_fn, send_fn=send_fn) == 2
    smoke = __import__("json").loads((tmp_path / "smoke.json").read_text())
    assert smoke["pass"] is False
    assert smoke["smoke_result"] == "fail"
    assert smoke["send_ok"] is False
    assert smoke["error_body"] == "upstream down"
