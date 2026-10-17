"""Billing-cycle meter window calculation with early/late slack (leg A2)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from .config import CreditConfig


def _nominal_anchor(year: int, month: int, anchor_day: int) -> datetime:
    """Return the nominal cycle anchor N_k at 00:00 UTC on ``anchor_day``."""
    return datetime(year, month, anchor_day, 0, 0, 0, tzinfo=UTC)


def _prev_month(year: int, month: int) -> tuple[int, int]:
    if month == 1:
        return year - 1, 12
    return year, month - 1


def _next_month(year: int, month: int) -> tuple[int, int]:
    if month == 12:
        return year + 1, 1
    return year, month + 1


def meter_window(t: datetime, cfg: CreditConfig) -> tuple[datetime, datetime]:
    """Return ``(start, reset_at)`` in UTC for tz-aware timestamp ``t``.

    Nominal anchors ``N_k`` sit at day ``cfg.cycle.anchor_day`` 00:00 UTC of each
    month. With ``a = early_slack_hours`` and ``b = late_slack_hours``, selects
    the latest ``N_j`` such that ``N_j + b <= t``, and returns:
    - ``start = N_j - a``
    - ``reset_at = N_{j+1} + b``
    """
    if not isinstance(t, datetime) or t.tzinfo is None or t.tzinfo.utcoffset(t) is None:
        raise ValueError("meter_window requires a timezone-aware datetime")

    t_utc = t.astimezone(UTC)
    anchor_day = cfg.cycle.anchor_day
    if not 1 <= anchor_day <= 28:
        raise ValueError(f"anchor_day must be in 1..28, got {anchor_day!r}")

    early_delta = timedelta(hours=cfg.cycle.early_slack_hours)
    late_delta = timedelta(hours=cfg.cycle.late_slack_hours)

    # N_j + b <= t_utc  <=>  N_j <= t_utc - b
    t_shifted = t_utc - late_delta
    cand = _nominal_anchor(t_shifted.year, t_shifted.month, anchor_day)
    if cand <= t_shifted:
        n_j = cand
        next_y, next_m = _next_month(t_shifted.year, t_shifted.month)
        n_j_plus_1 = _nominal_anchor(next_y, next_m, anchor_day)
    else:
        prev_y, prev_m = _prev_month(t_shifted.year, t_shifted.month)
        n_j = _nominal_anchor(prev_y, prev_m, anchor_day)
        n_j_plus_1 = cand

    start = n_j - early_delta
    reset_at = n_j_plus_1 + late_delta
    return start, reset_at
