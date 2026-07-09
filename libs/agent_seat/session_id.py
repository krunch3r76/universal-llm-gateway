"""Canonical cortex session-ID mint and validation helpers."""

from __future__ import annotations

import hashlib
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
    suffix: str | None = None,
) -> str:
    """Mint a unique session ID: ``{seat_slug}-YYYY-MM-DD-HHMMSS-{3hex}``.

    Pass *suffix* (3 hex chars) for deterministic derivation (JSONL start);
    omit for a fresh random suffix (boot / live mint).
    """
    t = at or datetime.now(UTC)
    hex_suffix = suffix if suffix is not None else secrets.token_hex(2)[:3]
    if not re.fullmatch(r"[0-9a-f]{3}", hex_suffix):
        raise ValueError(f"session_id suffix must be 3 hex chars, got {hex_suffix!r}")
    body = f"{seat_slug}-{t.strftime('%Y-%m-%d-%H%M%S')}-{hex_suffix}"
    if mode == SessionMintMode.INSPECT:
        return f"inspect-{body}"
    return body


def session_id_time_base(session_id: str) -> str:
    """Strip the 3-hex entropy suffix for timestamp-only comparisons."""
    return re.sub(r"-[0-9a-f]{3}$", "", session_id)


def derive_session_id_from_timestamp(
    agent: str,
    timestamp: str,
    *,
    deterministic: bool = False,
) -> str:
    """Derive a session ID from agent + ISO or date fragment timestamp.

    When *deterministic* is True and the timestamp parses, the 3-hex suffix is
    a stable hash of ``agent|iso`` so successive JSONL-start derivations do not
    flap (friction 23205). Unparseable timestamps still fall through to a live
    wall-clock mint unless the caller refuses that path.
    """
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
        if deterministic:
            iso = at.strftime("%Y-%m-%d-%H%M%S")
            digest = hashlib.sha256(f"{agent}|{iso}".encode()).hexdigest()[:3]
            return mint_session_id(agent, at=at, suffix=digest)
        return mint_session_id(agent, at=at)
    return mint_session_id(agent)
