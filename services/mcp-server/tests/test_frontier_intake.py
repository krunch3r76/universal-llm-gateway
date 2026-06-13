"""Unit tests for team_dispatch intake helpers (F16655 / F16656 / F16657).

Covers the extracted pure helpers in ``tools/_frontier_intake.py`` so the three
intake behaviors are verifiable without standing up FastMCP. Edge-case matrix
follows the gpt-5.5 reviewer consult (exec 55c64966) adopted as policy B+.
"""

from __future__ import annotations  # noqa: I001

from tools._frontier_intake import (
    normalize_dispatch_model,
    require_dispatch_thread_id,
)


# ── F16655 — model -mcp suffix strip ─────────────────────────────────────────


def test_strip_mcp_suffix_basic() -> None:
    assert normalize_dispatch_model("openai/gpt-5.5-mcp") == "openai/gpt-5.5"


def test_strip_mcp_suffix_noop_when_absent() -> None:
    assert normalize_dispatch_model("openai/gpt-5.5") == "openai/gpt-5.5"


def test_strip_mcp_suffix_none_passthrough() -> None:
    assert normalize_dispatch_model(None) is None


def test_strip_mcp_no_false_positive_on_other_suffixes() -> None:
    # Real catalog suffixes must be untouched.
    assert (
        normalize_dispatch_model("openai/gpt-5-search-api") == "openai/gpt-5-search-api"
    )
    assert normalize_dispatch_model("xai/grok-4.20-0309-reasoning") == (
        "xai/grok-4.20-0309-reasoning"
    )
    assert normalize_dispatch_model("hermes-3-...-16384-hybrid") == (
        "hermes-3-...-16384-hybrid"
    )


def test_strip_mcp_strips_only_one_trailing_suffix() -> None:
    assert normalize_dispatch_model("openai/foo-mcp-mcp") == "openai/foo-mcp"


# ── F16656 — dispatch_thread_id presence ─────────────────────────────────────


def test_dispatch_thread_id_present_ok_generate() -> None:
    assert require_dispatch_thread_id("generate", "dt-123") is None


def test_dispatch_thread_id_present_ok_to_thread() -> None:
    assert require_dispatch_thread_id("to_thread", "dt-123") is None


def test_dispatch_thread_id_missing_generate_rejected() -> None:
    err = require_dispatch_thread_id("generate", "")
    assert err is not None
    assert err["field"] == "dispatch_thread_id"
    assert "required" in err["error"]["message"]


def test_dispatch_thread_id_whitespace_rejected() -> None:
    err = require_dispatch_thread_id("generate", "   ")
    assert err is not None
    assert err["field"] == "dispatch_thread_id"


def test_dispatch_thread_id_required_for_to_thread_too() -> None:
    # Ticket said generate-only; relay forwards the key on both ops.
    err = require_dispatch_thread_id("to_thread", "")
    assert err is not None
    assert err["field"] == "dispatch_thread_id"


def test_dispatch_thread_id_not_required_for_handoff() -> None:
    assert require_dispatch_thread_id("handoff", "") is None
