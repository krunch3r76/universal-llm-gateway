"""Unit tests for normalize_session_summary_heading + seat-specific source error.

Covers the Postel's-law liberalization of the structural-layer heading
(close path no longer 422s on a near-miss heading) and the seat-specific
transcript_source.missing guidance.
"""

from __future__ import annotations

# Prime the package import order to avoid the latent dispatch_ops/__init__
# circular import that manifests only when session_close_validation is the
# import root (same pattern as test_session_close_handoff.py).
from cortex_store import db as _db  # noqa: F401
from cortex_store.dispatch_ops import ops_journals as _ops_journals  # noqa: F401
from cortex_store.session_close_validation import (
    _SUMMARY_HEADING_LITERAL,
    _validate_session_close_args,
    normalize_session_summary_heading,
)


def test_literal_heading_unchanged_no_warning() -> None:
    md = "## Session Summary\n\n**Decisions:** none\n"
    out, warning = normalize_session_summary_heading(md)
    assert out == md
    assert warning is None


def test_h1_heading_normalized_to_h2() -> None:
    md = "# Session Summary\n\n**Decisions:** one\n"
    out, warning = normalize_session_summary_heading(md)
    assert out.startswith(_SUMMARY_HEADING_LITERAL)
    assert "# Session Summary\n" not in out.replace(_SUMMARY_HEADING_LITERAL, "")
    assert warning is not None
    assert warning["kind"] == "session_summary_heading_normalized"


def test_trailing_colon_normalized() -> None:
    out, warning = normalize_session_summary_heading("### Session Summary:\nbody\n")
    assert _SUMMARY_HEADING_LITERAL in out
    assert "Session Summary:" not in out
    assert warning is not None


def test_extra_whitespace_normalized() -> None:
    out, warning = normalize_session_summary_heading("##  Session   Summary\nbody\n")
    assert _SUMMARY_HEADING_LITERAL in out
    assert warning is not None


def test_no_heading_prepended() -> None:
    md = "**Decisions:**\n1. did a thing\n"
    out, warning = normalize_session_summary_heading(md)
    assert out.startswith(_SUMMARY_HEADING_LITERAL)
    assert md.strip() in out
    assert warning is not None
    assert "prepended" in warning["detail"]


def test_bare_summary_heading_gets_prepend_not_rewrite() -> None:
    # "## Summary" lacks "Session" — must NOT be rewritten; heading prepended.
    md = "## Summary\nbody\n"
    out, warning = normalize_session_summary_heading(md)
    assert out.startswith(_SUMMARY_HEADING_LITERAL)
    assert "## Summary" in out
    assert warning is not None


def test_idempotent() -> None:
    once, _ = normalize_session_summary_heading("# Session Summary\nbody\n")
    twice, warning = normalize_session_summary_heading(once)
    assert twice == once
    assert warning is None


def test_empty_unchanged() -> None:
    out, warning = normalize_session_summary_heading("   \n  ")
    assert out == "   \n  "
    assert warning is None


def test_validate_args_accepts_missing_heading() -> None:
    # Heading presence is no longer a hard reject; only emptiness is.
    err = _validate_session_close_args(
        session_id="cursor-2026-06-02-1400",
        agent="cursor",
        transcript_jsonl_path="/some/path.jsonl",
        session_summary_md="**Decisions:** stuff happened\n",
        summary="A sufficiently long summary sentence.",
        transcript_depth="verbatim",
        emit_rejected=False,
    )
    assert err is None


def test_validate_args_rejects_empty_summary() -> None:
    err = _validate_session_close_args(
        session_id="cursor-2026-06-02-1400",
        agent="cursor",
        transcript_jsonl_path="/some/path.jsonl",
        session_summary_md="   ",
        summary="A sufficiently long summary sentence.",
        transcript_depth="verbatim",
        emit_rejected=False,
    )
    assert err is not None
    assert err["reason"] == "session_summary.invalid"


def test_source_missing_cursor_seat_hint() -> None:
    err = _validate_session_close_args(
        session_id="gemini-cursor-2026-06-02-1400",
        agent="gemini-cursor",
        transcript_jsonl_path=None,
        session_summary_md="## Session Summary\nbody\n",
        summary="A sufficiently long summary sentence.",
        transcript_depth="verbatim",
        emit_rejected=False,
    )
    assert err is not None
    assert err["reason"] == "transcript_source.missing"
    assert err["field"] == "transcript_jsonl_path"
    assert "Cursor seat" in err["hint"]


def test_source_missing_web_seat_hint() -> None:
    err = _validate_session_close_args(
        session_id="claude-web-2026-06-02-1400",
        agent="claude-web",
        transcript_jsonl_path=None,
        session_summary_md="## Session Summary\nbody\n",
        summary="A sufficiently long summary sentence.",
        transcript_depth="verbatim",
        emit_rejected=False,
    )
    assert err is not None
    assert err["reason"] == "transcript_source.missing"
    assert err["field"] == "transcript_md"
    assert "web/API seat" in err["hint"]
