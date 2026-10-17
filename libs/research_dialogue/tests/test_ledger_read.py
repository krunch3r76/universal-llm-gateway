from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from research_dialogue._types import Snapshot
from research_dialogue.ledger_read import check_ok, snapshot_from_payload

pytestmark = pytest.mark.offline


def _snap(**kw: object) -> Snapshot:
    base = dict(
        as_of_utc=datetime(2026, 1, 2, tzinfo=UTC),
        healthy=True,
        committed_usd=Decimal("1"),
        open_reservations_usd=Decimal("0"),
        window_start="2026-01-01T00:00:00+00:00",
        ledger_instance_id="inst-a",
        latch_active=False,
    )
    base.update(kw)
    return Snapshot(**base)  # type: ignore[arg-type]


def test_check_ok_unhealthy() -> None:
    s0 = _snap()
    bad = _snap(healthy=False)
    assert check_ok(bad, s0) == "ledger_unhealthy"


def test_unhealthy_payload_before_field_validation() -> None:
    snap = snapshot_from_payload({"healthy": False, "note": "down"})
    assert snap.healthy is False
    assert snap.committed_usd == Decimal("0")


def test_check_ok_latched() -> None:
    s0 = _snap()
    latched = _snap(latch_active=True)
    assert check_ok(latched, s0) == "ledger_latched"


def test_check_ok_frame_changed() -> None:
    s0 = _snap()
    rolled = _snap(window_start="2026-02-01T00:00:00+00:00")
    assert check_ok(rolled, s0) == "frame_changed"
    inst = _snap(ledger_instance_id="inst-b")
    assert check_ok(inst, s0) == "frame_changed"


def test_check_ok_stale_committed_regression() -> None:
    s0 = _snap()
    pre = _snap(committed_usd=Decimal("0.5"))
    assert (
        check_ok(pre, s0, last_post_committed=Decimal("1.0")) == "stale_read"
    )


def test_check_ok_stale_instance() -> None:
    s0 = _snap(ledger_instance_id="inst-b")
    pre = _snap(ledger_instance_id="inst-c")
    assert (
        check_ok(pre, s0, last_post_ledger_instance_id="inst-b") == "stale_read"
    )


def test_check_ok_stale_ledger_max_id() -> None:
    s0 = _snap()
    pre = _snap()
    assert (
        check_ok(
            pre,
            s0,
            last_post_ledger_max_id=10,
            pre_ledger_max_id=5,
        )
        == "stale_read"
    )


def test_check_ok_ok() -> None:
    s0 = _snap()
    s1 = _snap(as_of_utc=datetime(2026, 1, 2, 1, 0, tzinfo=UTC))
    assert check_ok(s1, s0) is None
