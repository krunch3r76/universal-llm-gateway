"""Tests for Anthropic billing-cycle meter window calculation (leg A2)."""

from __future__ import annotations

import dataclasses
import random
from datetime import UTC, datetime, timedelta, timezone

import pytest

from anthropic_credit.config import load_credit_config
from anthropic_credit.cycle_window import _next_month, _nominal_anchor, meter_window

pytestmark = pytest.mark.offline


def test_sanity_value_shipped_config() -> None:
    """Break covered: off-by-one month or wrong slack direction on shipped config."""
    cfg = load_credit_config()
    t = datetime(2026, 10, 17, 15, 0, 0, tzinfo=UTC)
    start, reset_at = meter_window(t, cfg)
    assert start == datetime(2026, 10, 3, 10, 0, 0, tzinfo=UTC)
    assert reset_at == datetime(2026, 11, 6, 0, 0, 0, tzinfo=UTC)


def test_naive_datetime_rejected() -> None:
    """Break covered: naive datetime silently treated as local/UTC causing tz drift."""
    cfg = load_credit_config()
    with pytest.raises(ValueError, match="timezone-aware"):
        meter_window(datetime(2026, 10, 17, 15, 0, 0), cfg)


def test_non_utc_tz_normalized_to_utc() -> None:
    """Break covered: non-UTC tz-aware input failing to normalize to UTC."""
    cfg = load_credit_config()
    tz_minus_7 = timezone(timedelta(hours=-7))
    t_local = datetime(2026, 10, 17, 8, 0, 0, tzinfo=tz_minus_7)
    start, reset_at = meter_window(t_local, cfg)
    assert start == datetime(2026, 10, 3, 10, 0, 0, tzinfo=UTC)
    assert reset_at == datetime(2026, 11, 6, 0, 0, 0, tzinfo=UTC)


def test_month_lengths_february_and_year_rollover() -> None:
    """Break covered: calendar rollover across Dec->Jan and Feb (leap and non-leap)."""
    base_cfg = load_credit_config()
    cfg = dataclasses.replace(
        base_cfg,
        cycle=dataclasses.replace(base_cfg.cycle, anchor_day=28),
    )

    # Jan 2, 2026 00:00Z is after Dec 28, 2025 + 48h (Dec 30 00:00Z)
    t_jan = datetime(2026, 1, 2, 0, 0, 0, tzinfo=UTC)
    start_jan, reset_jan = meter_window(t_jan, cfg)
    assert start_jan == datetime(2025, 12, 27, 10, 0, 0, tzinfo=UTC)
    assert reset_jan == datetime(2026, 1, 30, 0, 0, 0, tzinfo=UTC)

    # Non-leap Feb 2026: Feb 28 + 48h = Mar 2, 2026 00:00Z
    t_before_mar_roll = datetime(2026, 3, 1, 23, 59, 59, tzinfo=UTC)
    start_pre, reset_pre = meter_window(t_before_mar_roll, cfg)
    assert start_pre == datetime(2026, 1, 27, 10, 0, 0, tzinfo=UTC)
    assert reset_pre == datetime(2026, 3, 2, 0, 0, 0, tzinfo=UTC)

    t_at_mar_roll = datetime(2026, 3, 2, 0, 0, 0, tzinfo=UTC)
    start_post, reset_post = meter_window(t_at_mar_roll, cfg)
    assert start_post == datetime(2026, 2, 27, 10, 0, 0, tzinfo=UTC)
    assert reset_post == datetime(2026, 3, 30, 0, 0, 0, tzinfo=UTC)

    # Leap year Feb 2028: Feb 28 + 48h = Mar 1, 2028 00:00Z (because Feb 29 exists)
    t_leap = datetime(2028, 3, 1, 0, 0, 0, tzinfo=UTC)
    start_leap, reset_leap = meter_window(t_leap, cfg)
    assert start_leap == datetime(2028, 2, 27, 10, 0, 0, tzinfo=UTC)
    assert reset_leap == datetime(2028, 3, 30, 0, 0, 0, tzinfo=UTC)


def test_window_property_6000_seeded_cases() -> None:
    """Break covered: true reset R_k in [N_k - a, N_k + b] escaping [start, reset_at).

    Property (PLAN-r2 § 2.3): for random anchor_day in 1..28, random slack hours
    a and b, random true resets R_k in [N_k - a, N_k + b] and R_{k+1} in
    [N_{k+1} - a, N_{k+1} + b], and random request timestamp t in [R_k, R_{k+1}),
    meter_window(t, cfg) satisfies start <= R_k and t < reset_at.
    """
    rng = random.Random(20261017)
    base_cfg = load_credit_config()

    for _ in range(6000):
        year = rng.randint(2024, 2030)
        month = rng.randint(1, 12)
        anchor_day = rng.randint(1, 28)
        early_h = rng.randint(0, 24)
        late_h = rng.randint(0, 72)

        cfg = dataclasses.replace(
            base_cfg,
            cycle=dataclasses.replace(
                base_cfg.cycle,
                anchor_day=anchor_day,
                early_slack_hours=early_h,
                late_slack_hours=late_h,
            ),
        )

        n_k = _nominal_anchor(year, month, anchor_day)
        next_y, next_m = _next_month(year, month)
        n_k1 = _nominal_anchor(next_y, next_m, anchor_day)

        a_sec = early_h * 3600
        b_sec = late_h * 3600

        r_k = n_k + timedelta(seconds=rng.randint(-a_sec, b_sec))
        r_k1 = n_k1 + timedelta(seconds=rng.randint(-a_sec, b_sec))
        span_sec = int((r_k1 - r_k).total_seconds())
        assert span_sec > 0

        t = r_k + timedelta(seconds=rng.randint(0, span_sec - 1))
        start, reset_at = meter_window(t, cfg)

        assert start <= r_k, (
            f"start={start} > R_k={r_k} for t={t}, anchor={anchor_day}, "
            f"a={early_h}, b={late_h}"
        )
        assert start <= t < reset_at
