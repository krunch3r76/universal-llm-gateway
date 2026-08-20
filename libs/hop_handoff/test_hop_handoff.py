"""Shared hop-handoff body author.

Covers cadence structural-body shape (pattern-assert on the per-fire birth
id — not byte-frozen), the standing-handoff missing vs current branch, the
verb-source first-line TYPE token, I6 stamp echo equality, and uuid4
collision resistance of successor_birth_id.
"""

from __future__ import annotations

import os
import re

import pytest

from hop_handoff import (
    StandingHandoffFreshness,
    assess_standing_handoff,
    build_continuity_handoff_body,
    build_seat_registration_stamp,
    build_seat_stand_down_body,
    consume_time_wake_protocol,
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
    assert "STAND_DOWN" in body
    assert "Absence is not permission" in body
    assert f"URI: {_URI}" in body


def test_mission_line_omitted_when_absent() -> None:
    """No captured mission ⇒ no line — never fabricated."""
    body = _cadence_body()
    assert not any(line.startswith("mission:") for line in body.splitlines())


def test_mission_line_present_when_provided() -> None:
    body = _cadence_body(mission="Recover the operator-proxy continuity arc.")
    lines = body.splitlines()
    assert "mission: Recover the operator-proxy continuity arc." in lines
    # Mission sits in the structural header, before the prose / blank line.
    header_end = lines.index("")
    assert "mission: Recover the operator-proxy continuity arc." in lines[:header_end]


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
        assert "missing (file absent): default STAND_DOWN" in body
    else:
        assert (
            "Read the standing handoff URI above before trusting any wake prose."
            in body
        )
        assert "The S7 standing-handoff state file is absent." not in body
        assert "STAND_DOWN" in body


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


def test_seat_stand_down_body_reuses_bare_type_token_with_context() -> None:
    """G1 content-contract bind: bare TYPE: SEAT_STAND_DOWN, not a new token."""
    body = build_seat_stand_down_body(
        superseded_registration_id="reg-old",
        new_registration_id="reg-new",
        execution_id="exec-1",
        parent_thread=_THREAD,
        observed_at="2026-08-17T21:00:00Z",
    )
    assert body.startswith("TYPE: SEAT_STAND_DOWN\n")
    assert "superseded_registration_id: reg-old" in body
    assert "registration_id: reg-new" in body
    assert "execution_id: exec-1" in body
    assert f"parent_thread: {_THREAD}" in body
    assert "observed_at: 2026-08-17T21:00:00Z" in body
    assert "SEAT_STAND_DOWN_ACK" in body


def test_seat_stand_down_body_still_classifies_as_bare_non_ack() -> None:
    """Extra context lines must not change cse_session_ack TYPE-line classification."""
    from cdp_ask.cse_session_ack import marker_type

    body = build_seat_stand_down_body(
        superseded_registration_id="reg-old",
        new_registration_id="reg-new",
        execution_id="exec-1",
        parent_thread=_THREAD,
    )
    assert marker_type(body) is None


def test_assess_standing_handoff_distinguishes_missing_stale_current(
    tmp_path, monkeypatch
) -> None:
    """mtime classifier is the only consume-time ABSENT vs STALE signal in-tree."""
    path = tmp_path / f"{_THREAD}-standing-handoff.md"
    monkeypatch.setattr(
        "hop_handoff.standing_handoff.standing_handoff_path",
        lambda _tid: path,
    )
    missing = assess_standing_handoff(_THREAD, now=1000.0, stale_after_s=100.0)
    assert missing.status == "missing"
    assert missing.mtime_epoch is None
    path.write_text("holder\n", encoding="utf-8")
    os.utime(path, (900.0, 900.0))
    current = assess_standing_handoff(_THREAD, now=950.0, stale_after_s=100.0)
    assert current.status == "current"
    stale = assess_standing_handoff(_THREAD, now=1100.0, stale_after_s=100.0)
    assert stale.status == "stale"


def test_consume_time_protocol_missing_is_stand_down_not_permission() -> None:
    """Re-rank vs S2: absent sidecar defaults STAND_DOWN unless bus tip confirms."""
    text = consume_time_wake_protocol(thread_id=_THREAD)
    assert "STAND_DOWN" in text
    assert "Absence is not permission" in text
    assert "missing (file absent)" in text
    assert "stale: same as missing" in text
    assert _URI in text


def test_consume_time_protocol_missing_confirms_rank_from_bus_tip() -> None:
    """No sidecar yet + tip names this seat's successor_birth_id ⇒ not bare STAND_DOWN."""
    text = consume_time_wake_protocol(thread_id=_THREAD)
    assert "SEAT_REGISTRATION confirms your successor_birth_id" in text
    assert "establish rank from the tip" in text
    assert "same disambiguator as stale" in text


def test_consume_time_protocol_missing_later_seat_still_stand_down() -> None:
    """No sidecar + tip names a later seat ⇒ STAND_DOWN (cause 1 preserved)."""
    text = consume_time_wake_protocol(thread_id=_THREAD)
    assert "later successor_birth_id" in text
    assert "Absence is not permission when the live bus tip names a later" in text
