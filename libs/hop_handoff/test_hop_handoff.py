"""Shared hop-handoff body author.

Covers cadence structural-body shape (pattern-assert on the per-fire birth
id — not byte-frozen), the standing-handoff missing vs current branch, the
verb-source first-line TYPE token, I6 stamp echo equality, and uuid4
collision resistance of successor_birth_id.
"""

from __future__ import annotations

import re

import pytest

from hop_handoff import (
    StandingHandoffFreshness,
    build_continuity_handoff_body,
    build_seat_registration_stamp,
    is_successor_birth_id,
    mint_successor_birth_id,
    parse_successor_birth_id,
)

pytestmark = pytest.mark.offline

_THREAD = "7182"
_URI = f"cortex://notes/system/threads/{_THREAD}-standing-handoff.md"
_BIRTH_LINE_RE = re.compile(r"^successor_birth_id: ([0-9a-f]{32})$")


def _handoff(status: str) -> StandingHandoffFreshness:
    return StandingHandoffFreshness(
        status=status,
        uri=_URI,
        mtime_epoch=None if status == "missing" else 1.0,
        age_s=None if status == "missing" else 10.0,
    )


def _cadence_body(**kwargs: object) -> str:
    return build_continuity_handoff_body(
        thread_id=_THREAD,
        trigger="watch_seated_at",
        source="cursor-auto-hop-cadence",
        handoff=_handoff("current"),
        age_s=2000.0,
        threshold_s=1500.0,
        **kwargs,  # type: ignore[arg-type]
    )


def test_shared_author_pattern_asserts_successor_birth_id() -> None:
    """Per-fire token is pattern-asserted, not byte-frozen (keeps mint on L1)."""
    body = _cadence_body()
    lines = body.splitlines()
    assert lines[0] == "TYPE: CONTINUITY_HANDOFF"
    assert "source: cursor-auto-hop-cadence" in body
    assert "standing_handoff_freshness: current" in body
    birth_lines = [line for line in lines if line.startswith("successor_birth_id:")]
    assert len(birth_lines) == 1
    match = _BIRTH_LINE_RE.fullmatch(birth_lines[0])
    assert match is not None
    assert is_successor_birth_id(match.group(1))
    # Birth id lives in the structural header, before the prose / L2-adjacent blank.
    header_end = lines.index("")
    assert birth_lines[0] in lines[:header_end]
    assert "Identity key is" in body
    assert "successor_birth_id (this structural header)" in body
    assert "Identity is chat_url" not in body
    assert "wake-guide" in body
    assert "unobservable" in body


def test_pinned_birth_id_is_byte_stable_for_adapter_parity() -> None:
    pin = "a" * 32
    body = _cadence_body(successor_birth_id=pin)
    assert f"successor_birth_id: {pin}" in body
    again = _cadence_body(successor_birth_id=pin)
    assert body == again


@pytest.mark.parametrize("status", ["current", "stale", "missing"])
def test_missing_vs_current_handoff_branch(status: str) -> None:
    body = build_continuity_handoff_body(
        thread_id=_THREAD,
        trigger="watch_seated_at",
        source="cursor-auto-hop-cadence",
        handoff=_handoff(status),
        age_s=2000.0,
        threshold_s=1500.0,
        successor_birth_id="b" * 32,
    )
    assert f"standing_handoff_freshness: {status}" in body
    if status == "missing":
        assert "The S7 standing-handoff state file is absent." in body
        assert (
            "Read the standing handoff URI above before trusting any wake prose."
            not in body
        )
    else:
        assert (
            "Read the standing handoff URI above before trusting any wake prose."
            in body
        )
        assert "The S7 standing-handoff state file is absent." not in body


def test_verb_source_body_starts_with_type_token() -> None:
    """Verb-authored body still has TYPE: CONTINUITY_HANDOFF as first nonblank."""
    body = build_continuity_handoff_body(
        thread_id=_THREAD,
        trigger="mcp-restart-healthy",
        source="agent-bus-hop-verb",
        handoff=_handoff("missing"),
    )
    first = next(line for line in body.splitlines() if line.strip())
    assert first == "TYPE: CONTINUITY_HANDOFF"
    assert "source: agent-bus-hop-verb" in body
    assert "trigger: mcp-restart-healthy" in body
    assert parse_successor_birth_id(body)


def test_successor_birth_id_collision_resistance() -> None:
    """uuid4 hex mints are unique across concurrent-shaped fires (not a 1s clock)."""
    minted = [mint_successor_birth_id() for _ in range(200)]
    assert all(is_successor_birth_id(value) for value in minted)
    assert len(set(minted)) == 200
    bodies = [_cadence_body() for _ in range(50)]
    ids = [parse_successor_birth_id(body) for body in bodies]
    assert all(is_successor_birth_id(value) for value in ids)
    assert len(set(ids)) == 50


def test_stamp_echoes_hop_body_birth_id_for_equality_match() -> None:
    """Successor holding only first-turn tokens can equality-match the stamp."""
    hop_body = _cadence_body()
    birth_id = parse_successor_birth_id(hop_body)
    assert birth_id is not None
    stamp = build_seat_registration_stamp(
        successor_birth_id=birth_id,
        registration_id="reg-new",
        execution_id="exec-1",
        parent_thread=_THREAD,
        chat_url="https://claude.ai/chat/example",
        observed_at="2026-08-13T21:00:00Z",
    )
    assert stamp.startswith("TYPE: SEAT_REGISTRATION\n")
    assert parse_successor_birth_id(stamp) == birth_id
    assert parse_successor_birth_id(stamp) == parse_successor_birth_id(hop_body)
    assert "registration_id: reg-new" in stamp
    assert "chat_url: https://claude.ai/chat/example" in stamp
