from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cortex_store.session_close_debrief import (
    DebriefOutcome,
    attempt_session_close_debrief,
    compose_debrief_body,
    session_debrief_token,
)

pytest_plugins = ["cortex_store.test_session_close_handoff"]


def test_session_debrief_token_format() -> None:
    assert (
        session_debrief_token("cursor-2026-06-02-1200")
        == "[session:cursor-2026-06-02-1200]"
    )


def test_compose_debrief_body_includes_token_and_fields() -> None:
    body = compose_debrief_body(
        session_id="cursor-2026-06-02-1200",
        agent="cursor",
        summary="Shipped debrief auto-post",
        journal_row_id=42,
        transcript_depth="light",
        content_hash="sha256:abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789",
        domains=["cortex_store"],
        decisions=["Post-commit debrief boundary"],
        open_items=["Rebuild cortex-api"],
    )
    assert "[session:cursor-2026-06-02-1200]" in body
    assert "**Agent**: cursor" in body
    assert "**Decisions**:" in body
    assert "**Open items**:" in body
    assert "journal_row_id=42" in body


@patch.dict("os.environ", {}, clear=True)
def test_attempt_debrief_disabled_without_token() -> None:
    outcome = attempt_session_close_debrief(
        session_id="cursor-2026-06-02-1200",
        agent="cursor",
        summary="Test",
        journal_row_id=1,
        transcript_depth="none",
        content_hash=None,
    )
    assert outcome == DebriefOutcome(None, "disabled")


@patch.dict("os.environ", {"AGENT_BUS_TOKEN": "test-token"})
@patch("cortex_store.session_close_debrief._find_existing_debrief", return_value=799)
def test_attempt_debrief_skips_existing(_mock_find: MagicMock) -> None:
    outcome = attempt_session_close_debrief(
        session_id="cursor-2026-06-02-1200",
        agent="cursor",
        summary="Test",
        journal_row_id=1,
        transcript_depth="none",
        content_hash=None,
    )
    assert outcome.debrief_status == "skipped_existing"
    assert outcome.debrief_turn_number == 799


@patch.dict("os.environ", {"AGENT_BUS_TOKEN": "test-token"})
@patch("cortex_store.session_close_debrief._post_debrief", return_value=801)
@patch("cortex_store.session_close_debrief._find_existing_debrief", return_value=None)
def test_attempt_debrief_posts_when_no_existing(
    _mock_find: MagicMock, _mock_post: MagicMock
) -> None:
    outcome = attempt_session_close_debrief(
        session_id="cursor-2026-06-02-1200",
        agent="cursor",
        summary="Test",
        journal_row_id=1,
        transcript_depth="none",
        content_hash=None,
    )
    assert outcome.debrief_status == "posted"
    assert outcome.debrief_turn_number == 801
    assert outcome.debrief_body is None


@patch.dict("os.environ", {"AGENT_BUS_TOKEN": "test-token"})
@patch("cortex_store.session_close_debrief._post_debrief", return_value=None)
@patch("cortex_store.session_close_debrief._find_existing_debrief", return_value=None)
def test_attempt_debrief_failed_returns_body(
    _mock_find: MagicMock, _mock_post: MagicMock
) -> None:
    outcome = attempt_session_close_debrief(
        session_id="cursor-2026-06-02-1200",
        agent="cursor",
        summary="Test",
        journal_row_id=1,
        transcript_depth="none",
        content_hash=None,
    )
    assert outcome.debrief_status == "failed"
    assert outcome.debrief_turn_number is None
    assert outcome.debrief_body is not None
    assert "[session:cursor-2026-06-02-1200]" in outcome.debrief_body


@patch("cortex_store.routes.session_journals.attempt_session_close_debrief")
def test_idempotent_reclose_retries_debrief(
    mock_debrief: MagicMock,
    session_env: dict[str, Path],
) -> None:
    """Idempotent re-close still runs debrief so a prior failed post can retry."""
    from cortex_store.dispatch_ops import ops_journals
    from cortex_store.test_session_close_handoff import _session_summary

    mock_debrief.return_value = DebriefOutcome(802, "posted")
    summary = "First close for debrief idempotency test."
    session_id = "cursor-2026-06-02-1200"
    first = ops_journals._op_session_close(
        session_id=session_id,
        agent="cursor",
        session_summary_md=_session_summary(summary),
        summary=summary,
        transcript_depth="none",
        decisions=["Ship idempotent debrief retry"],
        open_items=["Deploy cortex_api"],
    )
    assert "error" not in first, first
    mock_debrief.assert_called_once()
    mock_debrief.reset_mock()
    mock_debrief.return_value = DebriefOutcome(803, "posted")

    second = ops_journals._op_session_close(
        session_id=session_id,
        agent="cursor",
        session_summary_md=_session_summary("Retry close attempt."),
        summary="Retry close attempt.",
        transcript_depth="none",
    )
    assert "error" not in second, second
    assert second["journal_row_id"] == first["journal_row_id"]
    assert second["debrief_status"] == "posted"
    assert second["debrief_turn_number"] == 803
    mock_debrief.assert_called_once()
    call_kwargs = mock_debrief.call_args.kwargs
    assert call_kwargs["summary"] == summary
    assert call_kwargs["decisions"] == ["Ship idempotent debrief retry"]
    assert call_kwargs["open_items"] == ["Deploy cortex_api"]
