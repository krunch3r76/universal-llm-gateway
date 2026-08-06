"""L2/L3 reporting contract — build_sdk_message, caller_auditable tiering, relay gaps."""

from __future__ import annotations

import pytest

from services.git_integration_worker.cursor_auto.caller_auditable import (
    caller_auditable,
)
from services.git_integration_worker.cursor_auto.closeout_relay_briefing import (
    finalize_relay_payload,
)
from services.git_integration_worker.cursor_auto.closeout_relay_common import (
    CloseoutRelayPayload,
)
from services.git_integration_worker.cursor_auto.closeout_relay_reporting import (
    amend_reporting_field_gaps,
)
from services.git_integration_worker.cursor_auto.directive import build_sdk_message
from services.git_integration_worker.cursor_auto.reporting_contract import (
    REPORTING_CONTRACT_BLOCK,
)
from services.git_integration_worker.cursor_auto.section2_fields import (
    SECTION2_FIELDS,
    section2_emit_line,
    section2_field_names,
)

_CONTRACTS = (
    "implement",
    "investigate",
    "verify",
    "execute",
    "confer",
    "propagate",
    "answer",
)

_DEFECTIVE_CLOSEOUT = """\
TYPE: CLOSEOUT
status: complete

**ac_verdict:**
1. AC1 — PASS — all green

**deltas_to_spec:** none — field not authored in §2 sidecar

**effects:** none
"""


@pytest.mark.parametrize("contract", _CONTRACTS)
def test_build_sdk_message_injects_reporting_contract_on_every_contract(
    contract: str,
) -> None:
    message = build_sdk_message("TYPE: DIRECTIVE\nscope: foo\nDo work.", contract=contract)
    assert "## REPORTING CONTRACT (mandatory)" in message
    assert "SCOPE DELTA" in message
    assert "ACCESS" in message
    assert "COVERAGE" in message
    assert "MODEL ACTUAL" in message
    assert "dispatch-report-discipline" in message
    assert "Negative answers are first-class" in message
    assert "VERBATIM FOR EVIDENCE" in message


def test_build_sdk_message_section2_emit_not_gated_on_confer_guard() -> None:
    """AC2 — §2 emit instruction lives in REPORTING CONTRACT, not confer guard."""
    body = "TYPE: DIRECTIVE\ncontract: confer\nscope: foo\nQuestion?"
    message = build_sdk_message(body, contract="confer")
    assert "Emit §2 fields inline in your closeout" in message
    assert "## Confer write fence" not in message


def test_build_sdk_message_confer_fence_when_guard_uris_present() -> None:
    body = (
        "TYPE: DIRECTIVE\ncontract: confer\nscope: foo\n"
        "evidence_required: cortex://notes/system/specs/example.md\n"
        "Question?"
    )
    message = build_sdk_message(body, contract="confer")
    assert "## Confer write fence (mandatory)" in message
    assert "cortex://notes/system/specs/example.md" in message


def test_caller_auditable_deny_by_default() -> None:
    assert caller_auditable(from_agent="mcp-claude-life") is False
    assert caller_auditable(from_agent="") is False
    assert caller_auditable(from_agent="email-bridge") is False


def test_caller_auditable_allowlist() -> None:
    assert caller_auditable(from_agent="web-anthropic") is True
    assert caller_auditable(from_agent="cursor") is True


def test_caller_auditable_keys_on_re_observability_not_lane() -> None:
    """The blind life seat is denied by its own address — not by denying the endpoint."""
    assert caller_auditable(from_agent="mcp-claude-life") is False
    assert caller_auditable(from_agent="web") is False


def test_tier_separation_same_defective_closeout_two_lanes() -> None:
    """AC7 — blind lane clamps; auditable lane deviation-only."""
    blind = amend_reporting_field_gaps(
        _DEFECTIVE_CLOSEOUT,
        status="complete",
        source="section2_sidecar",
        caller_auditable=False,
        model_substitution=False,
    )
    auditable = amend_reporting_field_gaps(
        _DEFECTIVE_CLOSEOUT,
        status="complete",
        source="section2_sidecar",
        caller_auditable=True,
        model_substitution=False,
    )
    assert blind.status == "complete"
    assert auditable.status == "complete"
    assert blind.relay_note is not None
    assert "reporting:blind_caller_missing_fields" in blind.relay_note
    assert "reporting:missing_access" in auditable.relay_note
    assert auditable.status == "complete"
    assert "reporting:missing_scope_delta" in blind.body
    assert "reporting:missing_access" in auditable.body
    assert "reporting:missing_scope_delta" in auditable.body


def test_finalize_relay_stamps_model_actual_on_substitution() -> None:
    body = """\
TYPE: CLOSEOUT
status: complete

**ac_verdict:** PASS

**SCOPE DELTA:** did the thing

**ACCESS:** full checkout

**COVERAGE:** n/a — no retrieval

**deltas_to_spec:** none
"""
    payload = finalize_relay_payload(
        CloseoutRelayPayload(body=body, status="complete", source="section2_sidecar"),
        wrapper_text=None,
        requested_model="composer-2.5",
        resolved_model="cursor/grok-4.5",
        caller_auditable=True,
    )
    assert "MODEL ACTUAL" in payload.body
    assert "requested=composer-2.5" in payload.body
    assert "resolved=cursor/grok-4.5" in payload.body


def test_reporting_contract_block_is_deliverable_text() -> None:
    assert REPORTING_CONTRACT_BLOCK.startswith("## REPORTING CONTRACT")
    assert "READ-ONLY TASKS STAY READ-ONLY" in REPORTING_CONTRACT_BLOCK
    assert "completion-provenance-discipline" in REPORTING_CONTRACT_BLOCK
    assert "positional implication" in REPORTING_CONTRACT_BLOCK


def test_reporting_contract_enumerates_every_projector_field() -> None:
    """Prompt surface and parser surface cannot drift — same generator, all fields."""
    for name in section2_field_names():
        assert f"`{name}`" in REPORTING_CONTRACT_BLOCK, f"§2 field {name!r} unnamed in prompt"
    assert section2_field_names() == tuple(field for field, _ in SECTION2_FIELDS)
    assert "etc." not in section2_emit_line()
    assert "verbatim" in section2_emit_line()
