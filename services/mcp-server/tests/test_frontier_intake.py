"""Unit tests for team_dispatch intake helpers (F16655 / F16656 / F16657).

Covers the extracted pure helpers in ``tools/_frontier_intake.py`` so the three
intake behaviors are verifiable without standing up FastMCP. Edge-case matrix
follows the gpt-5.5 reviewer consult (exec 55c64966) adopted as policy B+.
"""

from __future__ import annotations  # noqa: I001

from tools._frontier_intake import (
    normalize_dispatch_model,
    require_dispatch_thread_id,
    validate_dispatch_messages,
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


# ── F16657 — messages[].content shape (policy B+) ────────────────────────────


def _user(content: object) -> dict[str, object]:
    return {"role": "user", "content": content}


def test_messages_plain_string_ok() -> None:
    assert validate_dispatch_messages([_user("hello")]) is None


def test_messages_empty_list_rejected() -> None:
    err = validate_dispatch_messages([])
    assert err is not None
    assert err["field"] == "messages"


def test_messages_empty_string_rejected() -> None:
    err = validate_dispatch_messages([_user("")])
    assert err is not None
    assert err["field"] == "messages[0].content"


def test_messages_whitespace_only_rejected() -> None:
    err = validate_dispatch_messages([_user("   \n\t")])
    assert err is not None
    assert err["field"] == "messages[0].content"


def test_messages_none_content_rejected() -> None:
    err = validate_dispatch_messages([_user(None)])
    assert err is not None
    assert err["field"] == "messages[0].content"


def test_messages_missing_content_rejected() -> None:
    err = validate_dispatch_messages([{"role": "user"}])
    assert err is not None
    assert err["field"] == "messages[0].content"


def test_messages_text_block_dict_rejected_with_hint() -> None:
    err = validate_dispatch_messages([_user({"type": "text", "text": "hi"})])
    assert err is not None
    assert err["field"] == "messages[0].content"
    assert "content blocks" in err["error"]["message"]


def test_messages_single_text_block_list_rejected() -> None:
    err = validate_dispatch_messages([_user([{"type": "text", "text": "hi"}])])
    assert err is not None
    assert err["field"] == "messages[0].content"
    assert "content blocks" in err["error"]["message"]


def test_messages_text_plus_image_block_rejected() -> None:
    blocks = [
        {"type": "text", "text": "look"},
        {"type": "image", "source": {"data": "..."}},
    ]
    err = validate_dispatch_messages([_user(blocks)])
    assert err is not None
    assert err["field"] == "messages[0].content"


def test_messages_tool_result_block_rejected() -> None:
    err = validate_dispatch_messages([_user([{"type": "tool_result", "content": "x"}])])
    assert err is not None


def test_messages_non_dict_entry_rejected() -> None:
    err = validate_dispatch_messages(["just a string"])  # type: ignore[list-item]
    assert err is not None
    assert err["field"] == "messages[0]"


def test_messages_requires_a_user_role() -> None:
    err = validate_dispatch_messages([{"role": "assistant", "content": "hi"}])
    assert err is not None
    assert err["field"] == "messages"
    assert "user" in err["error"]["message"]


def test_messages_multiple_valid_with_user_ok() -> None:
    msgs = [
        {"role": "system", "content": "be terse"},
        {"role": "user", "content": "ping"},
    ]
    assert validate_dispatch_messages(msgs) is None
