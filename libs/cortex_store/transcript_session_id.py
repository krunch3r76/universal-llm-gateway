"""JSONL session-start → session_id derivation for session_close preflight.

Separated from ``transcript_assembly`` (verbatim markdown) so each module
stays ≤300 SLOC. Callers keep importing the public symbols from
``transcript_assembly`` (re-exported there for stable import paths).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

from agent_seat.session_id import (
    derive_session_id_from_timestamp,
    session_id_time_base,
)

_JSONL_TIMESTAMP_TAG_RE = re.compile(
    r"<timestamp>\s*([^<]+?)\s*</timestamp>", re.IGNORECASE
)
# Cursor harness emits both full and abbreviated months, often with a trailing
# ``(UTC-7)`` / ``(UTC)`` / ``(UTC+0)`` suffix — e.g.
# ``Thursday, Jul 9, 2026, 2:37 AM (UTC-7)``.
_CURSOR_NL_TIMESTAMP_RE = re.compile(
    r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s+"
    r"(January|February|March|April|May|June|July|August|September|"
    r"October|November|December|"
    r"Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\s+"
    r"(\d{1,2}),\s+(\d{4}),\s+"
    r"(\d{1,2}):(\d{2})\s+(AM|PM)"
    r"(?:\s*\(UTC(?P<tz_sign>[+-])?(?P<tz_hours>\d{1,2})?\))?",
    re.IGNORECASE,
)
_MONTH_TO_INT = {
    "january": 1,
    "jan": 1,
    "february": 2,
    "feb": 2,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "may": 5,
    "june": 6,
    "jun": 6,
    "july": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sep": 9,
    "sept": 9,
    "october": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "december": 12,
    "dec": 12,
}
_ISO_LIKE_TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:?\d{2}")


def _normalize_cursor_timestamp(raw: str) -> str:
    """Normalize Cursor harness timestamps to UTC ISO-ish strings for mint."""
    match = _CURSOR_NL_TIMESTAMP_RE.search(raw)
    if not match:
        return raw
    month_name = match.group(1)
    day = match.group(2)
    year = match.group(3)
    hour = match.group(4)
    minute = match.group(5)
    ampm = match.group(6)
    tz_sign = match.group("tz_sign")
    tz_hours = match.group("tz_hours")
    month = _MONTH_TO_INT[month_name.lower()]
    h = int(hour)
    if ampm.upper() == "AM":
        h = 0 if h == 12 else h
    else:
        h = 12 if h == 12 else h + 12
    dt = datetime(int(year), month, int(day), h, int(minute), 0, tzinfo=UTC)
    if tz_sign and tz_hours is not None:
        # ``(UTC-7)`` means local = UTC-7; convert local wall clock → UTC.
        offset_hours = int(tz_hours)
        if tz_sign == "-":
            dt = dt + timedelta(hours=offset_hours)
        else:
            dt = dt - timedelta(hours=offset_hours)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _extract_jsonl_start_timestamp(jsonl_path: Path) -> str | None:
    """Best-effort session-start timestamp from the first user turn in JSONL."""
    from cortex_store.transcript_assembly import _extract_user_text, _read_jsonl

    try:
        records = _read_jsonl(jsonl_path)
    except (OSError, ValueError):
        return None
    for record in records:
        if record.get("role") != "user":
            continue
        message = record.get("message") or {}
        content = message.get("content")
        if not isinstance(content, list):
            continue
        user_text = _extract_user_text(content)
        if not user_text:
            continue
        match = _JSONL_TIMESTAMP_TAG_RE.search(user_text)
        if match:
            return match.group(1).strip()
        break
    return None


def _jsonl_paths_by_mtime_desc(root: Path) -> list[Path]:
    """JSONL files under *root*, newest directory mtime first."""
    ranked: list[tuple[float, Path]] = []
    if not root.is_dir():
        return []
    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        jsonl = entry / f"{entry.name}.jsonl"
        if jsonl.is_file():
            ranked.append((jsonl.stat().st_mtime, jsonl))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [path for _, path in ranked]


def derive_session_id_from_jsonl_start(*, jsonl_path: Path, agent: str) -> str | None:
    """Derive ``{agent}-YYYY-MM-DD-HHMMSS-{3hex}`` from a stable JSONL start.

    Priority: (1) ``<timestamp>`` on the first user turn, (2) file birth time
    when the platform exposes ``st_birthtime``. Returns ``None`` when neither
    is available — callers MUST keep the boot-held ``session_id`` rather than
    minting from ``st_mtime`` (Linux appends race the mtime and re-mint a new
    suffix on every preflight/validate call; friction 21760 / 23205).
    """
    tagged = _extract_jsonl_start_timestamp(jsonl_path)
    if tagged:
        normalized = _normalize_cursor_timestamp(tagged)
        # derive_session_id_from_timestamp wall-clock-falls-back when the
        # string is not ISO-parseable — that re-mints a new suffix every
        # call (friction 23205). Refuse the fallback; keep boot-held ID.
        if not _ISO_LIKE_TIMESTAMP_RE.search(normalized):
            return None
        return derive_session_id_from_timestamp(
            agent,
            normalized,
            deterministic=True,
            conversation_uuid=jsonl_path.parent.name,
        )
    st = jsonl_path.stat()
    started = getattr(st, "st_birthtime", None)
    if started is None or started <= 0:
        return None
    dt = datetime.fromtimestamp(started, tz=UTC)
    # Birth-time path: deterministic suffix from agent|iso|uuid so
    # preflight copy-paste does not flap across retries, and two tabs
    # starting the same UTC second do not collide.
    return derive_session_id_from_timestamp(
        agent,
        dt.strftime("%Y-%m-%d %H:%M:%S"),
        deterministic=True,
        conversation_uuid=jsonl_path.parent.name,
    )


def derive_prior_session_id_from_jsonl_path(
    *, jsonl_path: Path, agent: str
) -> str | None:
    """Suggest ``prior_session_id`` from the second-newest transcript JSONL."""
    from cortex_store.transcript_assembly import _transcripts_root

    root = _transcripts_root()
    ordered = _jsonl_paths_by_mtime_desc(root)
    resolved = jsonl_path.resolve()
    try:
        idx = next(i for i, path in enumerate(ordered) if path.resolve() == resolved)
    except StopIteration:
        return None
    if idx + 1 >= len(ordered):
        return None
    return derive_session_id_from_jsonl_start(jsonl_path=ordered[idx + 1], agent=agent)


def session_id_timing_hint(
    *,
    session_id: str,
    jsonl_path: Path,
    agent: str,
) -> str | None:
    """Advisory note when ``session_id`` differs from a *stable* JSONL start.

    Boot-held ``session_id`` is authoritative (friction 23205) — this note is
    informational only and must NOT be placed on the failing ``warnings`` list.
    When JSONL has no stable start marker, return ``None``.
    """
    from_jsonl = derive_session_id_from_jsonl_start(jsonl_path=jsonl_path, agent=agent)
    if from_jsonl is None:
        return None
    if session_id_time_base(session_id) == session_id_time_base(from_jsonl):
        return None
    return (
        f"session_id {session_id!r} differs from JSONL session-start "
        f"{from_jsonl!r}; boot-held ID is authoritative — keep {session_id!r}. "
        f"Use JSONL-start {from_jsonl!r} only when no boot ID exists."
    )


__all__ = [
    "_normalize_cursor_timestamp",
    "derive_prior_session_id_from_jsonl_path",
    "derive_session_id_from_jsonl_start",
    "session_id_timing_hint",
]
