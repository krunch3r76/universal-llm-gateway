"""Logical-seat boot fetch wiring — agent vs capability separation."""

from __future__ import annotations

from typing import Any

from agent_seat.boot_identity import legacy_boot_identity

from tools.cortex_named_tools._boot_data_fetch import build_futures_spec
from tools.cortex_named_tools._boot_runner import _resolve_last_session


class _Recorder:
    def wrap(self, _name: str, fn: Any) -> Any:
        return fn


def test_build_futures_spec_logical_inbox_and_filters() -> None:
    logical = legacy_boot_identity("gpt", "web")
    logical = logical.__class__(
        family=logical.family,
        platform=logical.platform,
        agent="alpha-work",
        session_prefix="alpha-work",
        inbox_recipient="alpha-inbox",
        self_entity_id="seat:alpha-work",
        logical_seat_id="seat:alpha-work",
        house="9999",
    )
    profile = {
        "session_limit": 3,
        "include_deadlines": True,
        "include_review_queue": True,
    }
    spec = build_futures_spec(
        logical.capability_slug,
        profile,
        _Recorder(),
        identity=logical,
    )
    unread_path = spec["unread_toc"][-1]
    assert "to=alpha-inbox" in unread_path
    session_path = spec["sessions"][-1]
    assert "agent=alpha-work" in session_path
    skills_path = spec["skills"][-1]
    assert "for_agent=gpt-web" in skills_path


def test_build_futures_spec_uses_identity_self_entity_id() -> None:
    logical = legacy_boot_identity("gpt", "web").__class__(
        family="gpt",
        platform="web",
        agent="alpha-work",
        session_prefix="alpha-work",
        inbox_recipient="alpha-work",
        self_entity_id="seat:alpha-work",
        logical_seat_id="seat:alpha-work",
    )
    profile = {"self_reflections_limit": 3}
    spec = build_futures_spec("gpt-web", profile, _Recorder(), identity=logical)
    assert "self_reflections" in spec
    assert "entity_id=seat%3Aalpha-work" in spec["self_reflections"][-1]


def test_continuity_for_briefing_card_strips_foreign_chain() -> None:
    from tools.cortex_named_tools._boot_runner import _continuity_for_briefing_card

    logical = legacy_boot_identity("gpt", "web").__class__(
        family="gpt",
        platform="web",
        agent="alpha-work",
        session_prefix="alpha-work",
        inbox_recipient="alpha-work",
        self_entity_id="seat:alpha-work",
        logical_seat_id="seat:alpha-work",
    )
    extracted = {
        "continuity": {
            "continuity_chain": [
                "other-seat-2026-01-01-010101-abc",
                "alpha-work-2026-01-02-010101-def",
            ],
            "error": None,
        }
    }
    out = _continuity_for_briefing_card(logical, extracted)
    assert out is not None
    assert out["continuity_chain"] == ["alpha-work-2026-01-02-010101-def"]


def test_resolve_last_session_rejects_foreign_continuity() -> None:
    logical = legacy_boot_identity("gpt", "web").__class__(
        family="gpt",
        platform="web",
        agent="alpha-work",
        session_prefix="alpha-work",
        inbox_recipient="alpha-work",
        self_entity_id="seat:alpha-work",
        logical_seat_id="seat:alpha-work",
    )
    extracted = {
        "continuity": {"last_session": {"agent": "other-seat", "summary": "x"}},
        "sessions": [{"agent": "other-seat", "summary": "y"}],
    }
    assert _resolve_last_session(logical, extracted) is None
