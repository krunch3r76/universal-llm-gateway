"""Tests for boot-card deadline stale rendering (audit 2026-08-05 §A1)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from tools._boot_helpers._briefing_card_render import deadline_line


def test_deadline_line_recent_overdue() -> None:
    today = datetime(2026, 8, 5, tzinfo=ZoneInfo("America/Los_Angeles"))
    line = deadline_line(
        {
            "deadline_date": "2026-08-01",
            "deadline_name": "Recent",
            "matter_name": "Matter",
        },
        today,
    )
    assert "4d OVERDUE" in line
    assert "STALE" not in line


def test_deadline_line_stale_overdue() -> None:
    today = datetime(2026, 8, 5, tzinfo=ZoneInfo("America/Los_Angeles"))
    line = deadline_line(
        {
            "deadline_date": "2026-07-17",
            "deadline_name": "PG&E safety check",
            "matter_name": "PG&E",
        },
        today,
    )
    assert "STALE?" in line
    assert "verify still owed" in line
    assert "OVERDUE" not in line
