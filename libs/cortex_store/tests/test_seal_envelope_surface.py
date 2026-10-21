"""Seal-time meta.surface: grok pours keep grok; legacy surfaces stay cursor."""

from __future__ import annotations

import pytest
from continuity_tape.messages import (
    ContinuityMessagesEnvelope,
    EnvelopeMeta,
    seal_messages_sha256,
)

from cortex_store.verbatim_succession import build_seal_envelope_meta

pytestmark = pytest.mark.offline

_SEALED_AT = "2026-10-09T18:00:00+00:00"
_SESSION_ID = "grok-bot-test-sid"
_MESSAGES = [{"role": "user", "content": "hi"}]


def _envelope(*, surface: str) -> ContinuityMessagesEnvelope:
    return ContinuityMessagesEnvelope(
        messages=_MESSAGES,
        meta=EnvelopeMeta(
            surface=surface,
            transcript_id="a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            turn_count=1,
            message_count=len(_MESSAGES),
        ),
    )


def test_grok_pour_seals_as_grok() -> None:
    sealed = build_seal_envelope_meta(
        _envelope(surface="grok"),
        session_id=_SESSION_ID,
        sealed_at=_SEALED_AT,
    )
    assert sealed.meta.surface == "grok"


def test_cursor_pour_seals_as_cursor() -> None:
    sealed = build_seal_envelope_meta(
        _envelope(surface="cursor"),
        session_id=_SESSION_ID,
        sealed_at=_SEALED_AT,
    )
    assert sealed.meta.surface == "cursor"


def test_claude_ai_pour_still_seals_as_cursor_legacy() -> None:
    sealed = build_seal_envelope_meta(
        _envelope(surface="claude_ai"),
        session_id=_SESSION_ID,
        sealed_at=_SEALED_AT,
    )
    assert sealed.meta.surface == "cursor"


def test_explicit_surface_override_on_cursor_envelope() -> None:
    sealed = build_seal_envelope_meta(
        _envelope(surface="cursor"),
        session_id=_SESSION_ID,
        sealed_at=_SEALED_AT,
        surface="grok",
    )
    assert sealed.meta.surface == "grok"


def test_seal_stamps_digest_session_and_time_unchanged() -> None:
    env = _envelope(surface="grok")
    expected_sha = seal_messages_sha256(_MESSAGES)
    sealed = build_seal_envelope_meta(
        env,
        session_id=_SESSION_ID,
        sealed_at=_SEALED_AT,
        tools="marker",
    )
    assert sealed.meta.messages_sha256 == expected_sha
    assert sealed.meta.session_id == _SESSION_ID
    assert sealed.meta.sealed_at == _SEALED_AT
    assert sealed.meta.message_count == len(_MESSAGES)
    assert sealed.meta.tools == "marker"


def test_structured_grok_keeps_openai_over_marker_stamp() -> None:
    env = _envelope(surface="grok")
    env.meta.tools = "openai"
    block = [{"type": "thinking", "thinking": "x"}, {"type": "tool_use", "id": "t", "name": "n", "input": {}}]
    env.messages = [{"role": "assistant", "content": block}]
    sealed = build_seal_envelope_meta(
        env, session_id=_SESSION_ID, sealed_at=_SEALED_AT, tools="marker"
    )
    assert sealed.meta.tools == "openai"
    assert sealed.messages[0]["content"] == block
