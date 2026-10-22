"""Parse Google Messages list timestamps into absolute local times.

The page shows a relative label, not a datetime attribute. Clock times are
today. A weekday or a month-day is noon on that local day, which is the
resolution the list actually has.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

_CLOCK = re.compile(
    r"^(?P<h>\d{1,2}):(?P<m>\d{2})\s*(?P<ampm>AM|PM)$",
    re.IGNORECASE,
)
_MONTH = re.compile(
    r"^(?P<mon>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(?P<day>\d{1,2})$",
    re.IGNORECASE,
)
_WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}
_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}


def label_text(raw: str) -> str:
    """First line of a timestamp label. The node repeats the weekday."""
    text = (raw or "").replace("\u202f", " ").replace("\xa0", " ").strip()
    return text.splitlines()[0].strip() if text else ""


def parse_list_time(raw: str, now: datetime) -> datetime | None:
    """Absolute local time for a list label, or None when the label is unknown."""
    text = label_text(raw)
    if not text:
        return None
    if now.tzinfo is None:
        now = now.astimezone()
    lowered = text.lower()
    if lowered in {"now", "just now"}:
        return now

    clock = _CLOCK.match(text)
    if clock:
        hour = int(clock.group("h"))
        minute = int(clock.group("m"))
        ampm = clock.group("ampm").upper()
        if ampm == "PM" and hour != 12:
            hour += 12
        if ampm == "AM" and hour == 12:
            hour = 0
        stamp = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if stamp - now > timedelta(minutes=5):
            stamp -= timedelta(days=1)
        return stamp

    if lowered == "yesterday":
        day = (now - timedelta(days=1)).replace(
            hour=12, minute=0, second=0, microsecond=0
        )
        return day

    if lowered in _WEEKDAYS:
        target = _WEEKDAYS[lowered]
        delta = (now.weekday() - target) % 7
        day = (now - timedelta(days=delta)).replace(
            hour=12, minute=0, second=0, microsecond=0
        )
        return day

    month = _MONTH.match(text)
    if month:
        mon = _MONTHS[month.group("mon").lower()[:3]]
        day_n = int(month.group("day"))
        year = now.year
        try:
            stamp = now.replace(
                year=year,
                month=mon,
                day=day_n,
                hour=12,
                minute=0,
                second=0,
                microsecond=0,
            )
        except ValueError:
            return None
        if stamp - now > timedelta(days=1):
            stamp = stamp.replace(year=year - 1)
        return stamp
    return None


def parse_since(spec: str, now: datetime) -> datetime:
    """ISO local time, or a trailing h/d offset from now."""
    text = spec.strip()
    if text.endswith("h") and text[:-1].isdigit():
        return now - timedelta(hours=int(text[:-1]))
    if text.endswith("d") and text[:-1].isdigit():
        return now - timedelta(days=int(text[:-1]))
    stamp = datetime.fromisoformat(text)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=now.tzinfo)
    return stamp
