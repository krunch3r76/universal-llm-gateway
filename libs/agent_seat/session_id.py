"""Canonical cortex session-ID mint and validation helpers."""

from __future__ import annotations

import re
import secrets
from datetime import UTC, datetime
from enum import Enum

SESSION_ID_RE_SOURCE = r"^[a-z]+(-[a-z]+)*-\d{4}-\d{2}-\d{2}-\d{6}-[0-9a-f]{3}$"
SESSION_ID_RE = re.compile(SESSION_ID_RE_SOURCE)
SESSION_ID_EXAMPLES = (
    "claude-cursor-2026-05-17-045830-abc",
    "claude-web-2026-05-17-045830-1a2",
    "inspect-claude-cursor-2026-05-17-045830-f00",
)

_TIMESTAMP_PARSE_RE = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):?(\d{2})(?::(\d{2}))?"
)


class SessionMintMode(Enum):
    LIVE = "live"
    INSPECT = "inspect"


def mint_session_id(
    seat_slug: str,
    *,
    mode: SessionMintMode = SessionMintMode.LIVE,
    at: datetime | None = None,
) -> str:
    """Mint a unique session ID: ``{seat_slug}-YYYY-MM-DD-HHMMSS-{3hex}``."""
    t = at or datetime.now(UTC)
    suffix = secrets.token_hex(2)[:3]
    body = f"{seat_slug}-{t.strftime('%Y-%m-%d-%H%M%S')}-{suffix}"
    if mode == SessionMintMode.INSPECT:
        return f"inspect-{body}"
    return body


def session_id_time_base(session_id: str) -> str:
    """Strip the 3-hex entropy suffix for timestamp-only comparisons."""
    return re.sub(r"-[0-9a-f]{3}$", "", session_id)


def derive_session_id_from_timestamp(agent: str, timestamp: str) -> str:
    """Derive a session ID from agent + ISO or date fragment timestamp."""
    match = _TIMESTAMP_PARSE_RE.search(timestamp)
    if match:
        year, mon, day, hour, minute, second = match.groups()
        at = datetime(
            int(year),
            int(mon),
            int(day),
            int(hour),
            int(minute),
            int(second or "0"),
            tzinfo=UTC,
        )
        return mint_session_id(agent, at=at)
    return mint_session_id(agent)
