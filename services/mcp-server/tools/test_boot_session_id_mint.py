"""Unit tests for cortex_boot session-ID mint (friction 13697)."""

from __future__ import annotations

import re
from datetime import UTC, datetime

from agent_seat.session_id import SessionMintMode, mint_session_id

_FORMAT_RE = re.compile(r"^[a-z]+(-[a-z]+)*-\d{4}-\d{2}-\d{2}-\d{6}-[0-9a-f]{3}$")
_INSPECT_FORMAT_RE = re.compile(
    r"^inspect-[a-z]+(-[a-z]+)*-\d{4}-\d{2}-\d{2}-\d{6}-[0-9a-f]{3}$"
)


def test_mint_session_id_live_format() -> None:
    fixed = datetime(2026, 6, 10, 1, 28, 30, tzinfo=UTC)
    sid = mint_session_id("claude-cursor", at=fixed)
    assert sid.startswith("claude-cursor-2026-06-10-012830-")
    assert _FORMAT_RE.fullmatch(sid)


def test_mint_session_id_inspect_format() -> None:
    fixed = datetime(2026, 6, 10, 1, 28, 30, tzinfo=UTC)
    sid = mint_session_id(
        "claude-cursor",
        mode=SessionMintMode.INSPECT,
        at=fixed,
    )
    assert sid.startswith("inspect-claude-cursor-2026-06-10-012830-")
    assert _INSPECT_FORMAT_RE.fullmatch(sid)


def test_mint_session_id_rapid_calls_distinct() -> None:
    fixed = datetime(2026, 6, 10, 1, 28, 30, tzinfo=UTC)
    a = mint_session_id("claude-web", at=fixed)
    b = mint_session_id("claude-web", at=fixed)
    assert a != b
    assert _FORMAT_RE.fullmatch(a)
    assert _FORMAT_RE.fullmatch(b)
