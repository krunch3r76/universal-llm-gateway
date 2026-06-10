"""Session-ID regex and opened_at parsing (friction 13697)."""

from __future__ import annotations

import pytest

from agent_seat.session_id import SESSION_ID_RE
from cortex_store.routes.session_close_helpers import _parse_opened_at


@pytest.mark.parametrize(
    "session_id",
    [
        "claude-cursor-2026-06-10-012830-abc",
        "claude-web-2026-06-10-235959-fff",
        "inspect-claude-cursor-2026-06-10-000001-000",
    ],
)
def test_session_id_re_accepts_new_format(session_id: str) -> None:
    assert SESSION_ID_RE.fullmatch(session_id)


@pytest.mark.parametrize(
    "session_id",
    [
        "cursor-2026-05-17-0458",
        "claude-web-2026-05-17-045830",
        "claude-cursor-2026-06-10-012830-ABC",
        "claude-cursor-2026-06-10-01283-abc",
    ],
)
def test_session_id_re_rejects_legacy_or_malformed(session_id: str) -> None:
    assert SESSION_ID_RE.fullmatch(session_id) is None


def test_parse_opened_at_extracts_seconds_ignores_suffix() -> None:
    assert (
        _parse_opened_at("claude-cursor-2026-06-10-012830-abc")
        == "2026-06-10T01:28:30Z"
    )


def test_parse_opened_at_rejects_minute_format() -> None:
    assert _parse_opened_at("cursor-2026-05-17-0458") is None
