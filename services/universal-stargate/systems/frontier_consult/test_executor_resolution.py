"""Unit tests for executor + consult-review handoff advisories."""

from __future__ import annotations

from .executor_resolution import (
    derive_generate_review,
    derive_recommended_executor,
    derive_recommended_review,
    should_emit_executor_override_audit,
)

_IMPLEMENT_BODY = """\
<scope>Goal: x.</scope>
<invariants>x</invariants>
<task_guidance>## Acceptance criteria
1. It works.</task_guidance>
<corpus>x</corpus>
<mcp_capabilities>x</mcp_capabilities>
<output_format>Reply.</output_format>
"""


def _fm_packet(**fields: str) -> str:
    lines = ["---"]
    for key, value in fields.items():
        lines.append(f"{key}: {value}")
    lines.append("---")
    return "\n".join(lines) + "\n" + _IMPLEMENT_BODY


def test_ac1_implement_default_composer() -> None:
    result = derive_recommended_executor("implement", _IMPLEMENT_BODY)
    assert result["recommended_executor"] == "composer"
    assert result["recommended_executor_source"] == "server_default:contract_implement"
    assert result["executor_bindable"] is True
    assert result["executor_policy_warnings"] == []


def test_ac2_web_inline_pure_cortex_doc() -> None:
    packet = _fm_packet(
        executor_override="web-inline",
        executor_override_reason_code="pure_cortex_doc_edit",
    )
    result = derive_recommended_executor("implement", packet)
    assert result["recommended_executor"] == "web-inline"
    assert result["executor_bindable"] is False


def test_ac3_non_composer_without_valid_reason_coerced() -> None:
    packet = _fm_packet(executor_override="claude-opus-4-8")
    result = derive_recommended_executor("implement", packet)
    assert result["recommended_executor"] == "composer"
    assert result["executor_policy_warnings"]


def test_ac4_design_judgment_rescope_warning() -> None:
    packet = _fm_packet(
        executor_override="claude-opus-4-8",
        executor_override_reason_code="design_judgment_remaining",
    )
    result = derive_recommended_executor("implement", packet)
    assert result["recommended_executor"] == "composer"
    assert any(
        "not_mechanical_implement" in w for w in result["executor_policy_warnings"]
    )


def test_ac5_composer_thinking_collapsed() -> None:
    packet = _fm_packet(executor_override="composer-thinking")
    result = derive_recommended_executor("implement", packet)
    assert result["recommended_executor"] == "composer"
    assert "composer_variants_collapsed" in result["executor_policy_warnings"]


def test_ac6_consult_no_executor_recommendation() -> None:
    result = derive_recommended_executor("consult", _IMPLEMENT_BODY)
    assert result["recommended_executor"] is None


def test_ac9_consult_review_default_on() -> None:
    assert derive_recommended_review("consult") == "cross-family-reconcile:default-on"


def test_ac9_implement_no_review_default() -> None:
    assert derive_recommended_review("implement") is None


def test_ac4_generate_default_on_discriminator() -> None:
    assert (
        derive_generate_review("dispatch_surface", auto_review_child=False)
        == "cross-family-reconcile:default-on"
    )
    assert derive_generate_review("trivial", auto_review_child=False) is None
    assert derive_generate_review(None, auto_review_child=False) is None


def test_ac6_recursion_guard_child_marker() -> None:
    assert derive_generate_review("judgment_required", auto_review_child=True) is None


def test_honored_non_composer_with_gap_reason() -> None:
    packet = _fm_packet(
        executor_override="claude-opus-4-8",
        executor_override_reason_code="capability_gap",
        executor_override_reason="MCP tool shape compliance required",
    )
    result = derive_recommended_executor("implement", packet)
    assert result["recommended_executor"] == "claude-opus-4-8"


def test_audit_emit_non_composer_or_override() -> None:
    assert should_emit_executor_override_audit(
        handoff_contract="implement",
        recommended_executor="web-inline",
        override_supplied=False,
    )
    assert should_emit_executor_override_audit(
        handoff_contract="implement",
        recommended_executor="composer",
        override_supplied=True,
    )
    assert not should_emit_executor_override_audit(
        handoff_contract="consult",
        recommended_executor="composer",
        override_supplied=True,
    )
