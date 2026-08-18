"""Unit tests for team_dispatch intake helpers (F16655 / F16656 / F16657 / wrap).

Covers the extracted pure helpers in ``tools/_frontier_intake.py`` so intake
behaviors are verifiable without standing up FastMCP.
"""

from __future__ import annotations  # noqa: I001

import pytest
from tools._frontier_intake import (
    normalize_dispatch_model,
    reject_pointer_body_on_generate,
    reject_unsupported_packet_inputs,
    require_cursor_sdk_checkout_lane,
    require_dispatch_thread_id,
    validate_inline_prompt_inputs,
    validate_wrap_inputs,
)


pytestmark = pytest.mark.offline


# ── SF1 — inline prompt / sidecar_ref ─────────────────────────────────────────


def test_prompt_and_sidecar_ref_mutually_exclusive() -> None:
    err = validate_inline_prompt_inputs(
        "generate",
        "light-bounded",
        None,
        None,
        "brief",
        "cortex://notes/x.md",
    )
    assert err is not None
    assert err["field"] == "sidecar_ref"
    assert err["error"]["code"] == "multiple_prompt_sources"


def test_prompt_alone_is_clean() -> None:
    assert (
        validate_inline_prompt_inputs(
            "generate", "light-bounded", None, None, "brief", None
        )
        is None
    )


def test_packet_path_and_prompt_are_mutually_exclusive() -> None:
    err = validate_inline_prompt_inputs(
        "generate", "light-bounded", "tmp/p.md", None, "brief", None
    )
    assert err is not None
    assert err["field"] == "prompt"
    assert err["error"]["code"] == "multiple_prompt_sources"


@pytest.mark.parametrize("contract", ["implement", "wrap"])
def test_inline_prompt_rejected_on_non_prompt_contract(contract: str) -> None:
    err = validate_inline_prompt_inputs(
        "generate", contract, None, "todo:x", "brief", None
    )
    assert err is not None
    assert err["field"] == "prompt"
    assert err["error"]["code"] == "inline_prompt_not_supported"


def test_inline_prompt_rejected_on_handoff() -> None:
    err = validate_inline_prompt_inputs(
        "handoff", None, "tmp/p.md", None, "brief", None
    )
    assert err is not None
    assert err["field"] == "prompt"
    assert err["error"]["code"] == "inline_prompt_not_supported"


def test_sidecar_ref_admitted_on_to_thread() -> None:
    assert (
        validate_inline_prompt_inputs(
            "to_thread",
            "light-bounded",
            None,
            None,
            None,
            "cortex://notes/x.md",
        )
        is None
    )


# ── F23301 — pointer_body handoff-only ───────────────────────────────────────


def test_pointer_body_rejected_on_generate() -> None:
    err = reject_pointer_body_on_generate("generate", "the panel brief")
    assert err is not None
    assert err["field"] == "pointer_body"
    assert "handoff-only" in err["error"]["message"]


def test_pointer_body_rejected_on_to_thread() -> None:
    err = reject_pointer_body_on_generate("to_thread", "brief")
    assert err is not None
    assert err["field"] == "pointer_body"


def test_pointer_body_allowed_on_handoff() -> None:
    assert reject_pointer_body_on_generate("handoff", "custom pointer") is None


def test_pointer_body_absent_is_clean() -> None:
    assert reject_pointer_body_on_generate("generate", None) is None


# ── F16655 — model -mcp suffix strip ─────────────────────────────────────────


def test_strip_mcp_suffix_basic() -> None:
    assert normalize_dispatch_model("openai/gpt-5.5-mcp") == "openai/gpt-5.5"


def test_strip_mcp_suffix_noop_when_absent() -> None:
    assert normalize_dispatch_model("openai/gpt-5.5") == "openai/gpt-5.5"


def test_strip_mcp_suffix_none_passthrough() -> None:
    assert normalize_dispatch_model(None) is None


def test_strip_mcp_no_false_positive_on_other_suffixes() -> None:
    assert (
        normalize_dispatch_model("openai/gpt-5-search-api") == "openai/gpt-5-search-api"
    )
    assert normalize_dispatch_model("xai/grok-4.6") == (
        "xai/grok-4.6"
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
    err = require_dispatch_thread_id("to_thread", "")
    assert err is not None
    assert err["field"] == "dispatch_thread_id"


def test_dispatch_thread_id_not_required_for_handoff() -> None:
    assert require_dispatch_thread_id("handoff", "") is None


def test_dispatch_thread_id_exempt_for_wrap_generate() -> None:
    assert require_dispatch_thread_id("generate", "", contract="wrap") is None


# ── F17378 — packet_path on generate; source_ref implement-only ──────────────


def test_packet_path_ok_on_light_bounded_generate() -> None:
    assert (
        reject_unsupported_packet_inputs(
            "generate", "light-bounded", "tmp/p.md", None
        )
        is None
    )


def test_packet_path_ok_on_pure_mechanical_generate() -> None:
    assert (
        reject_unsupported_packet_inputs(
            "generate", "pure-mechanical", "tmp/p.md", None
        )
        is None
    )


def test_source_ref_rejected_on_pure_mechanical_generate() -> None:
    err = reject_unsupported_packet_inputs(
        "generate", "pure-mechanical", None, "todo:x"
    )
    assert err is not None
    assert err["field"] == "source_ref"


def test_source_ref_rejected_on_light_bounded_generate() -> None:
    err = reject_unsupported_packet_inputs(
        "generate", "light-bounded", None, "todo:x"
    )
    assert err is not None
    assert err["field"] == "source_ref"


def test_packet_path_ok_on_implement_generate() -> None:
    assert (
        reject_unsupported_packet_inputs(
            "generate", "implement", "tmp/p.md", None
        )
        is None
    )


def test_source_ref_ok_on_wrap_generate() -> None:
    assert (
        reject_unsupported_packet_inputs(
            "generate", "wrap", None, "todo:slug"
        )
        is None
    )


def test_packet_inputs_rejected_on_to_thread() -> None:
    err = reject_unsupported_packet_inputs(
        "to_thread", "light-bounded", "tmp/p.md", None
    )
    assert err is not None
    assert err["field"] == "packet_path"


def test_no_packet_inputs_passthrough() -> None:
    assert (
        reject_unsupported_packet_inputs(
            "generate", "light-bounded", None, None
        )
        is None
    )


def test_handoff_op_not_guarded() -> None:
    assert (
        reject_unsupported_packet_inputs(
            "handoff", None, "tmp/p.md", None
        )
        is None
    )


# ── contract=wrap intake guards ──────────────────────────────────────────────


def test_wrap_requires_source_ref_at_mcp() -> None:
    err = validate_wrap_inputs(
        "generate", "wrap", True, None, None
    )
    assert err is not None
    assert err["field"] == "source_ref"
    assert err["error"]["code"] == "wrap_requires_source_ref"


def test_wrap_with_packet_path_at_mcp() -> None:
    err = validate_wrap_inputs(
        "generate", "wrap", True, "tmp/p.md", "todo:slug"
    )
    assert err is not None
    assert err["field"] == "packet_path"
    assert err["error"]["code"] == "wrap_with_packet_path"


def test_wrap_role_not_admitted_at_mcp() -> None:
    err = validate_wrap_inputs(
        "generate", "wrap", False, None, "todo:slug"
    )
    assert err is not None
    assert err["field"] == "role"
    assert err["error"]["code"] == "wrap_role_not_admitted"


def test_wrap_rejected_on_to_thread_at_mcp() -> None:
    err = validate_wrap_inputs(
        "to_thread", "wrap", True, None, "todo:slug"
    )
    assert err is not None
    assert err["field"] == "contract"


def test_wrap_gating_misleading_knobs_rejected() -> None:
    for field_name, kwargs in (
        ("density_triage", {"density_triage": "judgment_required"}),
        (
            "review_opt_out_reason_code",
            {"review_opt_out_reason_code": "routine_single_subsystem"},
        ),
        ("auto_review_child", {"auto_review_child": True}),
    ):
        err = validate_wrap_inputs(
            "generate",
            "wrap",
            True,
            None,
            "todo:slug",
            **kwargs,
        )
        assert err is not None, field_name
        assert err["field"] == field_name
        assert err["error"]["code"] == "wrap_field_not_applicable"


def test_wrap_rejected_on_handoff_at_mcp() -> None:
    err = validate_wrap_inputs(
        "handoff", "wrap", True, None, "todo:slug"
    )
    assert err is not None
    assert err["field"] == "contract"


def test_wrap_valid_inputs_passthrough() -> None:
    assert (
        validate_wrap_inputs("generate", "wrap", True, None, "todo:slug") is None
    )


def test_cursor_sdk_checkout_lane_required_on_top_level_omit() -> None:
    err = require_cursor_sdk_checkout_lane(
        op="generate",
        seat="cursor-sdk",
        role=None,
        model=None,
        lane=None,
        nest_under=None,
        contract="implement",
    )
    assert err is not None
    assert err["error"]["code"] == "lane_required"
    assert err["field"] == "lane"


def test_cursor_sdk_checkout_lane_exempts_nest_wrap_and_named() -> None:
    assert (
        require_cursor_sdk_checkout_lane(
            op="generate",
            seat="cursor-sdk",
            role=None,
            model=None,
            lane=None,
            nest_under="parent-id",
            contract="implement",
        )
        is None
    )
    assert (
        require_cursor_sdk_checkout_lane(
            op="generate",
            seat="cursor-sdk",
            role=None,
            model=None,
            lane=None,
            nest_under=None,
            contract="wrap",
        )
        is None
    )
    assert (
        require_cursor_sdk_checkout_lane(
            op="generate",
            seat="cursor-sdk",
            role=None,
            model=None,
            lane="A",
            nest_under=None,
            contract="light-bounded",
        )
        is None
    )


def test_cursor_sdk_checkout_lane_required_on_model_only() -> None:
    err = require_cursor_sdk_checkout_lane(
        op="generate",
        seat=None,
        role=None,
        model="cursor/composer-2.5",
        lane=None,
        nest_under=None,
        contract="light-bounded",
    )
    assert err is not None
    assert err["error"]["code"] == "lane_required"
