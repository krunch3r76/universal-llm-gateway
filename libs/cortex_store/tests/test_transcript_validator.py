"""Unit tests for _validate_transcript_structure."""

from __future__ import annotations

from cortex_store.dispatch_ops.ops_journals import _validate_transcript_structure


def _clean_transcript() -> str:
    return (
        "# Transcript: cursor-2026-05-02-1200\n\n"
        "## Turn 1 — topic\n\n"
        "### User\nWhat does this function do?\n\n"
        "### Assistant\nIt validates transcript structure.\n\n"
        "## Session Summary\n**Decisions:** none\n"
    )


def test_clean_transcript_no_warnings() -> None:
    warnings = _validate_transcript_structure(_clean_transcript(), summary_len=20)
    assert warnings == []


def test_action_log_transcript() -> None:
    action_log = (
        "# Transcript: cursor-2026-05-02-1200\n\n"
        "## Session Summary\n"
        "I read the file.\n"
        "I then posted a reply.\n"
        "I also dispatched the pipeline.\n"
        "I ran the tests.\n"
        "I wrote the cortex assertion.\n"
        "I called the MCP tool.\n"
    )
    warnings = _validate_transcript_structure(action_log, summary_len=10)
    assert any("action-log" in w.lower() or "Action-log" in w for w in warnings)
    assert any("user" in w.lower() for w in warnings)


def test_no_user_voice_blocks() -> None:
    # user_blocks == 0 is a hard 422 upstream (transcript.hollow);
    # _validate_transcript_structure no longer duplicates that advisory.
    transcript = (
        "# Transcript: cursor-2026-05-02-1200\n\n"
        "## Turn 1\n\n"
        "### Assistant\nSome response here.\n\n"
        "## Session Summary\n**Summary:** foo\n"
    )
    warnings = _validate_transcript_structure(transcript, summary_len=10)
    assert warnings == []


def test_no_assistant_voice_blocks() -> None:
    transcript = (
        "# Transcript: cursor-2026-05-02-1200\n\n"
        "## Turn 1\n\n"
        "### User\nHello there.\n\n"
        "## Session Summary\n**Summary:** foo\n"
    )
    warnings = _validate_transcript_structure(transcript, summary_len=10)
    assert any("assistant" in w.lower() for w in warnings)


def test_canary4_transcript_shorter_than_summary() -> None:
    short = "**User:** hi\n**Assistant:** ok"
    warnings = _validate_transcript_structure(short, summary_len=len(short) + 100)
    assert any("Canary 4" in w for w in warnings)


def test_canary4_transcript_longer_than_summary_no_warning() -> None:
    transcript = _clean_transcript()
    warnings = _validate_transcript_structure(transcript, summary_len=5)
    assert not any("Canary 4" in w for w in warnings)


def test_truncated_but_marked_transcript() -> None:
    """Transcript with user+assistant voice but truncation marker: no warnings."""
    transcript = (
        "# Transcript: cursor-2026-05-02-1200\n\n"
        "## Turn 1\n\n"
        "### User\nDo the thing.\n\n"
        "### Assistant\nDone. [transcript truncated for length]\n\n"
        "## Session Summary\n**Summary:** short\n"
    )
    warnings = _validate_transcript_structure(transcript, summary_len=10)
    assert warnings == []
