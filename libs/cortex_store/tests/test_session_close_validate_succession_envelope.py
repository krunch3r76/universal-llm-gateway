"""Tests for succession envelope user-turn bypass in web close validation."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from continuity_tape.messages import ContinuityMessagesEnvelope, EnvelopeMeta
from fastapi import HTTPException

from cortex_store.models import SessionCloseRequest
from cortex_store.routes.session_close_validate import _validate_web_envelope

pytestmark = pytest.mark.offline

_SESSION_ID = "web-anthropic-2026-09-10-120000-abc"


def _assistant_only_envelope(*, coverage: str | None) -> ContinuityMessagesEnvelope:
    return ContinuityMessagesEnvelope(
        messages=[
            {"role": "assistant", "content": "Succession harvest reply text."},
        ],
        meta=EnvelopeMeta(
            surface="claude_ai",
            coverage=coverage,
            message_count=1,
            turn_count=1,
        ),
    )


def _succession_body(*, authority: bool) -> SessionCloseRequest:
    return SessionCloseRequest(
        session_id=_SESSION_ID,
        agent="web-anthropic",
        session_summary_md="## Session Summary\n\n**Decisions:** test\n**Open items:** none.",
        summary="Succession test close.",
        closed_by="succession",
        succession_seal_authority=authority,
    )


def test_succession_full_coverage_assistant_only_bypasses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bypass = MagicMock()
    monkeypatch.setattr(
        "cortex_store.events_tape.session_close_succession_user_turn_bypass",
        bypass,
    )
    _validate_web_envelope(
        _succession_body(authority=True),
        _assistant_only_envelope(coverage="full"),
    )
    bypass.assert_called_once_with(
        session_id=_SESSION_ID,
        closed_by="succession",
        coverage="full",
        message_count=1,
        user_turn_count=0,
    )


def test_succession_tail_coverage_still_422(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "cortex_store.routes.session_close_helpers._emit_rejected",
        lambda *args, **kwargs: None,
    )
    with pytest.raises(HTTPException) as exc:
        _validate_web_envelope(
            _succession_body(authority=True),
            _assistant_only_envelope(coverage="tail"),
        )
    assert exc.value.status_code == 422
    detail = exc.value.detail
    assert isinstance(detail, dict)
    assert detail.get("reason") == "transcript_messages.invalid"


def test_succession_absent_coverage_still_422(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "cortex_store.routes.session_close_helpers._emit_rejected",
        lambda *args, **kwargs: None,
    )
    with pytest.raises(HTTPException) as exc:
        _validate_web_envelope(
            _succession_body(authority=True),
            _assistant_only_envelope(coverage=None),
        )
    assert exc.value.status_code == 422
    detail = exc.value.detail
    assert isinstance(detail, dict)
    assert detail.get("reason") == "transcript_messages.invalid"


def test_succession_no_authority_still_422(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "cortex_store.routes.session_close_helpers._emit_rejected",
        lambda *args, **kwargs: None,
    )
    with pytest.raises(HTTPException) as exc:
        _validate_web_envelope(
            _succession_body(authority=False),
            _assistant_only_envelope(coverage="full"),
        )
    assert exc.value.status_code == 422
    detail = exc.value.detail
    assert isinstance(detail, dict)
    assert detail.get("reason") == "transcript_messages.invalid"
