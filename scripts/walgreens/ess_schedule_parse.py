"""Pure parsers for ESS My Schedule week rows."""

from __future__ import annotations

import re
from datetime import date

_WEEK_RE = re.compile(
    r"Week Of\s+([A-Za-z]+)\s+(\d{1,2}),\s+(\d{4})", re.I
)
_DATE_RE = re.compile(
    r"^(Mon|Tue|Wed|Thu|Fri|Sat|Sun),\s+(\d{1,2})\s+([A-Za-z]+)$"
)
_CLOCK_RE = re.compile(
    r"(\d{1,2}:\d{2})\s*([ap])m?\s*-\s*(\d{1,2}:\d{2})\s*([ap])m?",
    re.I,
)
_STORE_RE = re.compile(r"\b(\d{5})S\b")
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


def month_num(token: str) -> int:
    key = token[:3].lower()
    if key not in _MONTHS:
        raise ValueError(f"unknown month {token!r}")
    return _MONTHS[key]


def to_24h(hhmm: str, ampm: str) -> str:
    hour_s, minute_s = hhmm.split(":")
    hour = int(hour_s)
    minute = int(minute_s)
    suffix = ampm.lower()
    if suffix == "a":
        hour = 0 if hour == 12 else hour
    else:
        hour = hour if hour == 12 else hour + 12
    return f"{hour:02d}:{minute:02d}"


def parse_week_label(label: str) -> date:
    match = _WEEK_RE.search(label.replace("\xa0", " "))
    if not match:
        raise ValueError(f"unparsed week label {label!r}")
    return date(int(match.group(3)), month_num(match.group(1)), int(match.group(2)))


def iso_for_row(date_label: str, week_of: date) -> str:
    match = _DATE_RE.match(date_label.strip())
    if not match:
        raise ValueError(f"unparsed date label {date_label!r}")
    day = int(match.group(2))
    month = month_num(match.group(3))
    year = week_of.year
    if week_of.month == 12 and month == 1:
        year += 1
    elif week_of.month == 1 and month == 12:
        year -= 1
    return date(year, month, day).isoformat()


def parse_clocks(text: str) -> tuple[str, str] | None:
    match = _CLOCK_RE.search(text.replace("\xa0", " "))
    if not match:
        return None
    return to_24h(match.group(1), match.group(2)), to_24h(match.group(3), match.group(4))


def parse_store(text: str) -> tuple[str, str] | None:
    match = _STORE_RE.search(text)
    if not match:
        return None
    code = f"{match.group(1)}S"
    return code, str(int(match.group(1)))


_LOCATION_STOPS = (
    " Pharmacy Open:",
    " Store Open:",
    " HR Alert:",
    " Shift Alert:",
)


def location_label(text: str, store_code: str) -> str:
    compact = " ".join(text.replace("\xa0", " ").split())
    marker = f"{store_code} - "
    tail = compact.split(marker, 1)[1] if marker in compact else compact
    for stop in _LOCATION_STOPS:
        if stop in tail:
            tail = tail.split(stop, 1)[0]
    return tail.strip()


def parse_day(date_label: str, row_text: str, week_of: date) -> dict:
    iso = iso_for_row(date_label, week_of)
    blob = row_text.replace("\xa0", " ")
    if re.search(r"Not Scheduled", blob):
        return {
            "date": iso,
            "date_label": date_label,
            "scheduled": False,
        }
    clocks = parse_clocks(blob)
    store = parse_store(blob)
    if clocks is None or store is None:
        raise ValueError(f"scheduled row missing clocks/store: {blob!r}")
    store_code, store_number = store
    role = "Pharmacist: Work" if "Pharmacist: Work" in blob else None
    return {
        "date": iso,
        "date_label": date_label,
        "scheduled": True,
        "start_local": clocks[0],
        "end_local": clocks[1],
        "store_code": store_code,
        "store_number": store_number,
        "location_label": location_label(blob, store_code),
        "role": role,
    }
