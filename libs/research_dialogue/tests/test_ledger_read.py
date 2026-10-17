from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from research_dialogue._types import Snapshot
from research_dialogue.ledger_read import check_ok

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


def test_check_ok_stale_read() -> None:
    s0 = _snap()
    t0 = datetime(2026, 1, 2, 12, 0, tzinfo=UTC)
    stale = _snap(as_of_utc=t0)
    prev = t0 + timedelta(seconds=1)
    assert check_ok(stale, s0, previous_response_at=prev) == "stale_read"


def test_check_ok_ok() -> None:
    s0 = _snap()
    s1 = _snap(as_of_utc=datetime(2026, 1, 2, 1, 0, tzinfo=UTC))
    assert check_ok(s1, s0) is None
