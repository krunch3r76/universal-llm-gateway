"""Parameter coercion and session-window resolution for event-store operations.

The helpers in this module centralize permissive user-input coercion shared by
named operations. They preserve the invariant that invalid optional values fall
back to safe defaults instead of raising, while session-aware windows prefer the
most recent ``system.started`` boundary when available.
"""

from __future__ import annotations

import time
from typing import Any

from universal_logging import get_logger

from .store import EventStore

logger = get_logger(__name__)
_SESSION_BOUNDARY_SIGNAL = "system.started"


def _coerce_limit(value: Any, default: int = 20) -> int:
    """Coerce a user-provided row limit to a bounded positive integer.

    Invalid values return ``default``. Valid values are clamped to ``[1, 500]``
    so named operations cannot accidentally request unbounded result sets.
    """
    try:
        limit = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(limit, 500))


def _coerce_minutes(value: Any) -> int | None:
    """Coerce an optional minute window to a bounded positive integer.

    ``None`` or invalid values return ``None``, which signals callers to use
    session-aware default-window semantics instead of a caller-specified window.
    """
    if value is None:
        return None
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        return None
    return max(1, min(minutes, 24 * 60))


def _signal_match_sql(signal: str) -> tuple[str, str]:
    """Map a user signal pattern to a SQL predicate fragment and bind value.

    Pattern syntax (matched against the ``signal`` column):

    - ``*`` — glob wildcard; mapped to LIKE ``%``. Literal ``%``/``_`` in the
      input are escaped (via ``ESCAPE '\\'``) so they match themselves, since a
      glob author does not expect ``_`` to act as a single-char wildcard
      (e.g. ``team_dispatch.*`` matches only the literal ``team_dispatch``).
    - ``%`` — raw SQL LIKE; the caller opted into LIKE semantics, so ``%`` and
      ``_`` keep their LIKE meaning and nothing is escaped.
    - otherwise — exact equality (``=``).

    Returns ``(predicate_fragment, bind_value)`` where ``predicate_fragment``
    is everything after ``signal `` in the WHERE clause and contains a single
    ``?`` placeholder bound to ``bind_value``.
    """
    if "*" in signal:
        escaped = signal.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return "LIKE ? ESCAPE '\\'", escaped.replace("*", "%")
    if "%" in signal:
        return "LIKE ?", signal
    return "= ?", signal


# Unix-seconds timestamps for dates through year 5138 are strictly below this;
# millisecond timestamps for 1973+ are at or above it. Used to detect the
# seconds-scale ``since_ts`` callers pass against ``ts_unix_ms`` columns.
_SECONDS_TS_EXCLUSIVE_MAX = 10**11


def _coerce_since_ts(value: Any) -> int | None:
    """Coerce an optional ``since_ts`` value to Unix milliseconds.

    Seconds-scale integers (strictly below ``1e11``) are multiplied by 1000
    so a filter against ``ts_unix_ms`` actually cuts. Values already in
    milliseconds pass through. Invalid values return ``None`` so callers can
    fall back to the active session boundary without surfacing parameter
    parsing errors to agents.
    """
    if value is None:
        return None
    try:
        ts = int(value)
    except (TypeError, ValueError):
        return None
    if 0 < ts < _SECONDS_TS_EXCLUSIVE_MAX:
        converted = ts * 1000
        logger.info(
            "since_ts seconds-scale %s converted to milliseconds %s",
            ts,
            converted,
        )
        return converted
    return ts


async def _get_session_start_ts(store: EventStore) -> int | None:
    """Return the Unix-millisecond timestamp of the latest session boundary.

    The event-store session boundary is the newest ``system.started`` signal.
    ``None`` is returned when the store has no such event, allowing callers to
    choose an operation-specific fallback window.
    """
    rows = await store.query(
        "SELECT MAX(ts_unix_ms) AS ts FROM events WHERE signal = ?",
        (_SESSION_BOUNDARY_SIGNAL,),
        limit=1,
    )
    if rows and rows[0].get("ts") is not None:
        return int(rows[0]["ts"])
    return None


async def _resolve_window_minutes_and_cutoff(
    params: dict[str, Any],
    store: EventStore,
    *,
    default_minutes: int = 5,
) -> tuple[int, int]:
    """Resolve an operation window into ``(minutes, cutoff_ts_ms)``.

    A valid ``params["minutes"]`` wins. Otherwise the window expands from the
    latest session boundary through now; when no boundary exists, the supplied
    ``default_minutes`` value is used. The cutoff is always computed relative to
    the resolved minute count.
    """
    minutes = _coerce_minutes(params.get("minutes"))
    if minutes is None:
        session_start_ts = await _get_session_start_ts(store)
        if session_start_ts is not None:
            elapsed_ms = int(time.time() * 1000) - session_start_ts
            minutes = max(1, elapsed_ms // 60_000 + 1)
        else:
            minutes = default_minutes
    cutoff = int(time.time() * 1000) - (minutes * 60 * 1000)
    return minutes, cutoff
