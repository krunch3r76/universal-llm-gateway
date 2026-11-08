"""Card read-path temporal eligibility (interval semantics for current projection).

Pure Python interval checks plus a connection-local SQLite UDF so current-card
SQL filters ``valid_from``/``valid_until`` before ``LIMIT``, without lexical
ISO comparison or SQLite datetime truncation.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime
from typing import Final

CURRENT_TEMPORAL_WHERE: Final[str] = (
    "superseded_by IS NULL AND assertion_interval_contains(valid_from, valid_until, ?)"
)

_UDF_NAME: Final[str] = "assertion_interval_contains"


def card_as_of_utc() -> str:
    """UTC ``as_of`` instant for one card fetch (ISO-8601, microsecond precision)."""
    return datetime.now(UTC).isoformat()


def _parse_instant(raw: object) -> datetime:
    """Parse a bound or ``as_of`` value; raise ``ValueError`` when non-null but invalid."""
    if raw is None:
        raise ValueError("missing instant")
    if isinstance(raw, datetime):
        dt = raw
    elif isinstance(raw, str):
        s = raw.strip()
        if not s:
            raise ValueError("empty instant")
        if len(s) == 10 and s[4] == "-" and s[7] == "-":
            d = date.fromisoformat(s)
            dt = datetime(d.year, d.month, d.day, tzinfo=UTC)
        else:
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            try:
                dt = datetime.fromisoformat(s)
            except (ValueError, OverflowError) as exc:
                raise ValueError("unparseable instant") from exc
    else:
        raise ValueError(f"unsupported instant type: {type(raw).__name__}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    try:
        return dt.astimezone(UTC)
    except OverflowError as exc:
        raise ValueError("instant out of range") from exc


def _parse_optional_bound(raw: object) -> datetime | None:
    """Only SQL ``NULL`` is an open endpoint; every other value must parse."""
    if raw is None:
        return None
    return _parse_instant(raw)


def assertion_interval_contains(
    valid_from: object,
    valid_until: object,
    as_of: object,
) -> bool:
    """True when ``as_of`` lies in ``[valid_from, valid_until)`` (null endpoints open).

    Non-null but unparseable bounds, or ``valid_from >= valid_until``, fail closed.
    """
    try:
        as_of_dt = _parse_instant(as_of)
    except ValueError:
        return False
    try:
        start = _parse_optional_bound(valid_from)
        end = _parse_optional_bound(valid_until)
    except ValueError:
        return False
    if start is not None and end is not None and start >= end:
        return False
    if start is not None and as_of_dt < start:
        return False
    if end is not None and as_of_dt >= end:
        return False
    return True


def instant_key(raw: object) -> datetime:
    """Parse a timestamp for ordering (public alias of ``_parse_instant``)."""
    return _parse_instant(raw)


def install_card_temporal_function(conn: sqlite3.Connection) -> None:
    """Register ``assertion_interval_contains`` on ``conn`` (idempotent per connection)."""

    def _udf(valid_from: object, valid_until: object, as_of: object) -> int:
        return 1 if assertion_interval_contains(valid_from, valid_until, as_of) else 0

    conn.create_function(_UDF_NAME, 3, _udf, deterministic=True)
