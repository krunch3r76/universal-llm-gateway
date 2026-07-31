"""Regex validation for cortex session IDs (friction 13697)."""

from __future__ import annotations

from agent_seat.session_id import SESSION_ID_RE, SESSION_ID_RE_SOURCE

from cortex_store.routes.session_close_helpers import _parse_opened_at


def test_session_id_re_accepts_new_shape() -> None:
    assert SESSION_ID_RE.match("claude-cursor-2026-06-10-012830-abc")
    assert SESSION_ID_RE.match("claude-web-2026-05-17-045830-1a2")
    assert SESSION_ID_RE.match("inspect-claude-cursor-2026-05-17-045830-f00")


def test_session_id_re_rejects_minute_only() -> None:
    assert not SESSION_ID_RE.match("web-2026-05-27-1009")


def test_session_id_re_rejects_second_only_no_suffix() -> None:
    assert not SESSION_ID_RE.match("web-2026-05-27-100900")


def test_session_id_re_rejects_malformed() -> None:
    assert not SESSION_ID_RE.match("web-2026-05-27")
    assert not SESSION_ID_RE.match("Web-2026-05-27-100900-abc")
    assert not SESSION_ID_RE.match("web-2026-05-27-100900-abcd")


def test_session_id_re_source_is_new_shape_only() -> None:
    assert SESSION_ID_RE_SOURCE.endswith(r"-\d{6}-[0-9a-f]{3}$")


def test_parse_opened_at_hhmmss_with_suffix() -> None:
    assert (
        _parse_opened_at("claude-cursor-2026-06-10-012830-abc")
        == "2026-06-10T01:28:30Z"
    )


def test_parse_opened_at_hhmmss_without_suffix() -> None:
    assert _parse_opened_at("cursor-2026-05-27-100000") == "2026-05-27T10:00:00Z"
