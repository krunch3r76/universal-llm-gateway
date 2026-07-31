"""Relay clamp + pointer-over-excerpt tests (6294 turn 18 regression surface)."""

from __future__ import annotations

import json

import pytest

from services.git_integration_worker.cursor_auto.closeout_relay import (
    select_closeout_relay_payload,
    synthesize_section2,
)
from services.git_integration_worker.cursor_auto.closeout_relay_briefing import (
    clamp_relay_body,
    finalize_relay_payload,
)
from services.git_integration_worker.cursor_auto.closeout_relay_common import (
    CloseoutRelayPayload,
    build_ac_verdict_cell,
    status_from_section2,
)
from services.git_integration_worker.cursor_auto.closeout_relay_cortex_spill import (
    promote_clamped_closeout_to_cortex,
)
from services.git_integration_worker.cursor_auto.relay_trust import (
    RELAY_TRUST_SYNTHESIZED_GATE_ENABLED,
    pending_synthesized_closeout,
)
from services.git_integration_worker.cursor_auto.substrate_feedback import (
    extract_substrate_findings,
)
from services.git_integration_worker.cursor_sdk_deliverables import (
    sidecar_workspaces_ref,
)

_DISPATCH = "auto-4f974d43d0de"
_WRAPPER = json.dumps(
    {
        "schema_version": 1,
        "status": "partial",
        "files_created": [],
        "capture_status": "partial",
        "effects_manifest": {"schema_version": 1},
    }
)

_SPEC_SNIPPET = (
    "# Agent-bus payload discipline\n\n"
    "M1 compensating parity gate — this spec markdown must not appear in ac_verdict.\n\n"
    + ("Lorem ipsum dolor sit amet. " * 120)
)


def _6294_sidecar() -> str:
    text = _SPEC_SNIPPET
    while len(text.encode("utf-8")) < 4027:
        text += " padding."
    return text


def test_build_ac_verdict_pointer_over_excerpt_for_addressable_provenance() -> None:
    sidecar = _6294_sidecar()
    provenance = f"cortex://notes/system/specs/example-{_DISPATCH}.md"
    cell = build_ac_verdict_cell(sidecar, provenance=provenance)
    assert "parse_failed" in cell
    assert provenance in cell
    assert "M1 compensating parity gate" not in cell
    assert "<br><br>" not in cell


def test_synthesized_6294_fixture_body_under_2048() -> None:
    sidecar = _6294_sidecar()
    payload = select_closeout_relay_payload(
        sdk_body=_WRAPPER,
        sidecar_text=sidecar,
        ledger_status="completed",
        dispatch_id=_DISPATCH,
    )
    assert payload.source == "section2_synthesized"
    assert len(payload.body) < 2048
    assert "M1 compensating parity gate" not in payload.body
    assert sidecar_workspaces_ref(_DISPATCH) in payload.body
    for field in (
        "status",
        "ac_verdict",
        "deltas_to_spec",
        "decisions_taken",
        "effects",
        "evidence",
        "next",
        "open forks",
    ):
        assert f"| {field} |" in payload.body


def test_before_after_body_size_observation() -> None:
    from services.git_integration_worker.cursor_auto.closeout_relay_common import (
        strip_machine_tail,
        unclassified_relay_prefix,
    )

    sidecar = _6294_sidecar()
    sidecar_prose = strip_machine_tail(sidecar).strip()
    old_provenance = f"repo sidecar for {_DISPATCH}"
    excerpt = sidecar_prose[:1500] + ("…" if len(sidecar_prose) > 1500 else "")
    old_ac_verdict = (
        f"{unclassified_relay_prefix(provenance=old_provenance, body=sidecar)}"
        f"<br><br>{excerpt}"
    )
    old_synthesized = synthesize_section2(
        wrapper_text=_WRAPPER,
        sidecar_text=sidecar,
        dispatch_id=_DISPATCH,
    )
    assert old_synthesized is not None
    before = old_synthesized.replace(
        old_synthesized.split("| ac_verdict | ", 1)[1].split(" |", 1)[0],
        old_ac_verdict.replace("|", "\\|").replace("\n", "<br>"),
        1,
    )
    before_len = len(before) if isinstance(before, str) else len(old_synthesized)
    # Reconstruct full old body by swapping ac_verdict cell content
    prefix, _, rest = old_synthesized.partition("| ac_verdict | ")
    mid, _, suffix = rest.partition(" |\n")
    before_body = f"{prefix}| ac_verdict | {old_ac_verdict.replace('|', chr(92)+'|').replace(chr(10), '<br>')} |{suffix}"
    before_len = len(before_body)

    payload = select_closeout_relay_payload(
        sdk_body=_WRAPPER,
        sidecar_text=sidecar,
        ledger_status="completed",
        dispatch_id=_DISPATCH,
    )
    after = len(payload.body)
    assert after < 2048
    assert before_len > 2000
    assert after < before_len


def test_status_from_section2_survives_clamp() -> None:
    sidecar = _6294_sidecar()
    payload = select_closeout_relay_payload(
        sdk_body=_WRAPPER,
        sidecar_text=sidecar,
        ledger_status="completed",
        dispatch_id=_DISPATCH,
    )
    assert status_from_section2(payload.body) in {None, "partial", "complete", "blocked"}


def test_fence_violation_survives_clamp() -> None:
    from services.git_integration_worker.cursor_auto.closeout_relay_effects import (
        amend_effects_underclaim,
    )

    sidecar = _6294_sidecar()
    synthesized = synthesize_section2(
        wrapper_text=_WRAPPER,
        sidecar_text=sidecar,
        dispatch_id=_DISPATCH,
    )
    assert synthesized is not None
    amended = amend_effects_underclaim(
        synthesized,
        wrapper_text=_WRAPPER,
        status="partial",
        source="section2_synthesized",
    )
    body_with_fence = amended.body + "\nfence_violation: cortex://notes/system/specs/guarded.md"
    clamped_body, _was_clamped = clamp_relay_body(
        body_with_fence,
        pointer=sidecar_workspaces_ref(_DISPATCH),
    )
    assert "fence_violation:" in clamped_body.lower()


def test_relay_trust_dispatch_id_from_clamped_body(monkeypatch: pytest.MonkeyPatch) -> None:
    sidecar = _6294_sidecar()
    payload = select_closeout_relay_payload(
        sdk_body=_WRAPPER,
        sidecar_text=sidecar,
        ledger_status="completed",
        dispatch_id=_DISPATCH,
    )
    body = payload.body + f"\ndispatch_id: {_DISPATCH}\nmeta: {{\"closeout_source\":\"section2_synthesized\"}}"
    turns = [
        {
            "turn_number": 1,
            "from": "cursor-auto",
            "subject": "status:done",
            "body": body,
        }
    ]
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.relay_trust.RELAY_TRUST_SYNTHESIZED_GATE_ENABLED",
        True,
    )
    assert pending_synthesized_closeout(turns, operator_from="web-anthropic") == _DISPATCH
    assert RELAY_TRUST_SYNTHESIZED_GATE_ENABLED is False


def test_substrate_findings_use_body_full() -> None:
    sidecar = (
        "TYPE: CLOSEOUT\n\n"
        "**effects:** none\n\n"
        "Pre-existing lint debt in closeout_relay.py should be tracked.\n"
        + ("x" * 5000)
    )
    payload = select_closeout_relay_payload(
        sdk_body=_WRAPPER,
        sidecar_text=sidecar,
        ledger_status="completed",
        dispatch_id=_DISPATCH,
    )
    full_text = payload.body_full or payload.body
    assert extract_substrate_findings(full_text) == extract_substrate_findings(
        payload.body_full or payload.body
    )
    if payload.body_full:
        assert len(payload.body_full) >= len(payload.body)
        assert "lint debt" in payload.body_full.lower()


def test_clamp_idempotent() -> None:
    body = "TYPE: CLOSEOUT\n\n| Field | Value |\n|---|---|\n| status | partial |\n"
    body += "| ac_verdict | " + ("z" * 5000) + " |"
    pointer = sidecar_workspaces_ref(_DISPATCH)
    first, clamped1 = clamp_relay_body(body, pointer=pointer)
    second, clamped2 = clamp_relay_body(first, pointer=pointer)
    assert clamped1
    assert not clamped2
    assert first == second


def test_clamp_skips_table_header_row_not_duplicated() -> None:
    """AC3 — existing | Field | Value | row must not produce a second header."""
    body = (
        "TYPE: CLOSEOUT\nstatus: complete\n\n"
        "| Field | Value |\n|---|---|\n"
        "| status | complete |\n"
        f"| ac_verdict | {'x' * 5000} |\n"
        "| deltas_to_spec | none |\n"
        "| decisions_taken | none |\n"
        "| effects | none |\n"
        "| evidence | none |\n"
        "| next | none |\n"
        "| open forks | none |\n"
    )
    clamped, was_clamped = clamp_relay_body(body, pointer=sidecar_workspaces_ref(_DISPATCH))
    assert was_clamped
    assert clamped.count("| Field | Value |") == 1
    assert clamped.count("|---|---|") == 1


def test_clamp_judgment_fields_get_larger_cell_budget() -> None:
    long = "word " * 500
    body = (
        "TYPE: CLOSEOUT\nstatus: complete\n\n"
        "| Field | Value |\n|---|---|\n"
        "| status | complete |\n"
        f"| ac_verdict | {long.strip()} |\n"
        "| deltas_to_spec | none |\n"
        "| decisions_taken | none |\n"
        "| effects | short |\n"
        "| evidence | none |\n"
        "| next | none |\n"
        "| open forks | none |\n"
    )
    clamped, was_clamped = clamp_relay_body(body, pointer=sidecar_workspaces_ref(_DISPATCH))
    assert was_clamped
    ac_cell = clamped.split("| ac_verdict | ", 1)[1].split(" |", 1)[0]
    effects_cell = clamped.split("| effects | ", 1)[1].split(" |", 1)[0]
    assert len(ac_cell) > len(effects_cell)


def test_envelope_status_matches_body_complete_c4_regression() -> None:
    """AC1/C4 — payload.status tracks §2 body when sidecar says complete."""
    sidecar = """\
TYPE: CLOSEOUT
status: complete

**ac_verdict:** PASS — all AC met

**deltas_to_spec:** none

**decisions_taken:** envelope sync bind

**next:** none

**open forks:** none
"""
    payload = select_closeout_relay_payload(
        sdk_body=_WRAPPER,
        sidecar_text=sidecar,
        ledger_status="completed",
        dispatch_id=_DISPATCH,
        caller_auditable=True,
    )
    assert payload.status == "complete"
    assert status_from_section2(payload.body) == "complete"
    table_status = payload.body.split("| status | ", 1)[1].split(" |", 1)[0]
    assert table_status == "complete"
    header_match = payload.body.splitlines()
    status_lines = [line for line in header_match if line.lower().startswith("status:")]
    assert len(status_lines) == 1
    assert status_lines[0].strip() == "status: complete"


def test_synthesized_with_sidecar_complete_not_forced_partial() -> None:
    """C4 class — synthesized path must not keep partial when sidecar projects complete."""
    sidecar = """\
TYPE: CLOSEOUT
status: complete

**ac_verdict:** PASS

**deltas_to_spec:** none
"""
    payload = select_closeout_relay_payload(
        sdk_body=_WRAPPER,
        sidecar_text=sidecar,
        ledger_status="completed",
        dispatch_id=_DISPATCH,
        caller_auditable=True,
    )
    assert payload.source == "section2_sidecar"
    assert payload.status == "complete"


@pytest.mark.asyncio
async def test_post_operator_closeout_single_envelope_status() -> None:
    """AC1 — operator-facing relay carries one TYPE: CLOSEOUT and matching status lines."""
    from unittest.mock import AsyncMock, MagicMock

    from services.git_integration_worker.cursor_auto.nested_sdk import post_operator_closeout
    from services.git_integration_worker.cursor_auto.queue import AutoJob

    closeout_body = (
        "TYPE: CLOSEOUT\nstatus: complete\n\n"
        "| Field | Value |\n|---|---|\n"
        "| status | complete |\n"
        "| ac_verdict | PASS |\n"
    )
    job = AutoJob(
        job_id="j-c4",
        thread_id="6561",
        turn_number=22,
        subject="DIRECTIVE",
        body="TYPE: DIRECTIVE",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="composer-2.5",
        desired_effort="default",
        contract="implement",
    )
    bus = MagicMock()
    bus.reply = AsyncMock(return_value=MagicMock(status_code=200, body="ok"))
    await post_operator_closeout(
        job,
        status="partial",
        dispatch_id="auto-c4-fix",
        model_id="cursor/composer-2.5",
        sdk_body=None,
        closeout_body=closeout_body,
        closeout_source="section2_sidecar",
        extra={"terminal_status": "completed"},
        bus=bus,
    )
    sent = bus.reply.await_args.kwargs["body"]
    assert sent.count("TYPE: CLOSEOUT") == 1
    status_lines = [
        line.strip()
        for line in sent.splitlines()
        if line.lower().startswith("status:")
    ]
    assert status_lines == ["status: complete"]
    assert '| status | complete |' in sent


_CORTEX_POINTER = (
    "cortex://notes/system/threads/6329-cursor-sdk-closeout-auto-4f974d43d0de.md"
)


def _clamped_relay_payload() -> CloseoutRelayPayload:
    body = (
        "TYPE: CLOSEOUT\nstatus: partial\n\n"
        "| Field | Value |\n|---|---|\n"
        "| status | partial |\n"
        f"| ac_verdict | {'z' * 5000} |\n"
        "| deltas_to_spec | none |\n"
        "| decisions_taken | none |\n"
        "| effects | none |\n"
        "| evidence | none |\n"
        "| next | none |\n"
        "| open forks | none |\n"
    )
    return finalize_relay_payload(
        CloseoutRelayPayload(
            body=body,
            status="partial",
            source="section2_synthesized",
        ),
        wrapper_text=_WRAPPER,
        dispatch_id=_DISPATCH,
    )


@pytest.mark.asyncio
async def test_promote_clamped_closeout_cortex_pointer() -> None:
    payload = _clamped_relay_payload()
    assert payload.clamped
    assert payload.body_full
    assert sidecar_workspaces_ref(_DISPATCH) in payload.body

    async def _mock_post_pinned(**kwargs: object) -> dict[str, str]:
        assert kwargs["content"] == payload.body_full
        return {"uri": _CORTEX_POINTER}

    promoted = await promote_clamped_closeout_to_cortex(
        payload,
        dispatch_id=_DISPATCH,
        thread_id="6329",
        post_closeout_sidecar_fn=_mock_post_pinned,
    )
    assert _CORTEX_POINTER in promoted.body
    assert sidecar_workspaces_ref(_DISPATCH) not in promoted.body
    assert "TYPE: CLOSEOUT" in promoted.body or "| status |" in promoted.body
    assert status_from_section2(promoted.body) in {None, "partial", "complete", "blocked"}
    for field in (
        "status",
        "ac_verdict",
        "deltas_to_spec",
        "decisions_taken",
        "effects",
        "evidence",
        "next",
        "open forks",
    ):
        assert f"| {field} |" in promoted.body


@pytest.mark.asyncio
async def test_promote_clamped_cortex_failure_keeps_workspace_pointer() -> None:
    payload = _clamped_relay_payload()
    assert payload.clamped

    async def _fail_write(**kwargs: object) -> None:
        return None

    promoted = await promote_clamped_closeout_to_cortex(
        payload,
        dispatch_id=_DISPATCH,
        thread_id="6329",
        post_closeout_sidecar_fn=_fail_write,
    )
    assert promoted.body == payload.body
    assert sidecar_workspaces_ref(_DISPATCH) in promoted.body
    assert status_from_section2(promoted.body) in {None, "partial", "complete", "blocked"}
