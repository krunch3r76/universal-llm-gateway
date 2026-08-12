"""Lane-A status_claim vs envelope measurement — arc 7070 specimen tests."""

from __future__ import annotations

import json
from pathlib import Path

from implement_admission.spec import CloseoutStatus, WorkOutcome

from services.git_integration_worker.cursor_auto.closeout_plane_probe import (
    annotate_checkpoint_claim_discrepancy,
    annotate_status_claim_discrepancy,
    checkpoint_dispositions_equivalent,
    merge_plane_discrepancy_markers,
    merge_plane_register_markers,
    status_dispositions_equivalent,
)
from services.git_integration_worker.cursor_auto.closeout_status_polarity import (
    classify_status_incomplete_class,
    merge_plane_legend_markers,
    resolve_status_disagreement_authority,
)
from services.git_integration_worker.cursor_auto.closeout_relay_common import (
    resolve_measurement_status_from_wrapper,
    resolve_relay_status,
)
from services.git_integration_worker.cursor_auto.lane_a_status import (
    extract_status_claim,
)
from services.git_integration_worker.cursor_sdk_authored_status_reconcile import (
    reconcile_structured_with_authored,
)
from services.git_integration_worker.cursor_sdk_closeout import (
    SdkRunOutcome,
    build_implement_closeout_body,
)
from services.git_integration_worker.cursor_sdk_deliverables import (
    sidecar_workspaces_ref,
)


def test_status_dispositions_equivalent_normalizes_failed_and_gated() -> None:
    assert status_dispositions_equivalent("blocked", "failed")
    assert status_dispositions_equivalent("blocked", "gated")
    assert status_dispositions_equivalent("complete", "complete")


def test_specimen_independent_agreement_silent() -> None:
    """Independent agreement — claim and measurement both complete → silence."""
    assert (
        annotate_status_claim_discrepancy(
            claim="complete",
            measurement="complete",
        )
        is None
    )


def test_specimen_contaminated_agreement_fires() -> None:
    """Contaminated agreement — claim partial vs uncapped machine complete stays visible."""
    marker = annotate_status_claim_discrepancy(
        claim="partial",
        measurement="complete",
    )
    assert marker == "status_claim@§2 partial while status@infra complete"
    merged = merge_plane_register_markers(marker)
    assert merged == "plane-register: status_claim@§2 partial while status@infra complete"
    assert merge_plane_discrepancy_markers(marker) is None


def test_specimen_independent_disagreement_fires() -> None:
    """Independent disagreement — claim complete vs machine partial → plane-legend."""
    marker = annotate_status_claim_discrepancy(
        claim="complete",
        measurement="partial:capture",
    )
    assert marker == (
        "status_claim@§2 complete while status@infra partial:capture"
    )
    assert merge_plane_discrepancy_markers(marker) is None
    assert merge_plane_legend_markers(marker) == (
        "plane-legend: status_claim@§2 complete while status@infra partial:capture"
    )


def test_absent_claim_emits_no_status_marker() -> None:
    """Absent ≠ disagree — blank claim must not emit status marker."""
    assert annotate_status_claim_discrepancy(claim=None, measurement="partial") is None
    assert annotate_status_claim_discrepancy(claim="", measurement="partial") is None
    assert annotate_status_claim_discrepancy(claim="   ", measurement="partial") is None


def test_specimen_capture_driven_complete_x_partial_auto_889de52ed385_shape() -> None:
    """Capture/measurement incompleteness — unverified + capture unavailable."""
    incomplete_class = classify_status_incomplete_class(
        status=CloseoutStatus.PARTIAL,
        work_outcome=WorkOutcome.UNVERIFIED,
        capture_status="unavailable",
        escalation_harvest="none",
        deviations=["capture:sidecar_absent", "degraded:sdk_git_probe_absent"],
    )
    assert incomplete_class == "capture"
    wrapper = json.dumps(
        {
            "schema_version": 1,
            "status": "partial",
            "status_incomplete_class": "capture",
            "work_outcome": "unverified",
            "capture_status": "unavailable",
        }
    )
    assert resolve_measurement_status_from_wrapper(wrapper) == "partial:capture"
    marker = annotate_status_claim_discrepancy(
        claim="complete",
        measurement="partial:capture",
    )
    assert merge_plane_legend_markers(marker) is not None
    assert merge_plane_discrepancy_markers(marker) is None


def test_specimen_work_driven_complete_x_partial_auto_84c1c42a3720_shape() -> None:
    """Work incompleteness — checks_failed + land:lane_b_unlanded."""
    incomplete_class = classify_status_incomplete_class(
        status=CloseoutStatus.PARTIAL,
        work_outcome=WorkOutcome.CHECKS_FAILED,
        capture_status="partial",
        escalation_harvest="none",
        deviations=["land:lane_b_unlanded"],
    )
    assert incomplete_class == "work"
    wrapper = json.dumps(
        {
            "schema_version": 1,
            "status": "partial",
            "status_incomplete_class": "work",
            "work_outcome": "checks_failed",
            "capture_status": "partial",
            "deviations": ["land:lane_b_unlanded"],
        }
    )
    assert resolve_measurement_status_from_wrapper(wrapper) == "partial:work"
    marker = annotate_status_claim_discrepancy(
        claim="complete",
        measurement="partial:work",
    )
    assert merge_plane_legend_markers(marker) is not None
    authority = resolve_status_disagreement_authority(
        claim="complete",
        measurement="partial:work",
        work_outcome="checks_failed",
        deviations=["land:lane_b_unlanded"],
    )
    assert authority is not None
    assert authority.work_outcome == "measure"
    assert authority.ac_pass == "measure"
    assert authority.next_step == "deviations_qualified_measure"


def test_status_disagreement_authority_bare_losses_named() -> None:
    """Bare claim and bare measure both lose on next-step when unqualified."""
    authority = resolve_status_disagreement_authority(
        claim="complete",
        measurement="partial",
        deviations=[],
    )
    assert authority is not None
    assert authority.next_step == "bare_measure"


def test_checkpoint_dispositions_equivalent_authored_cortex_digest_optional() -> None:
    """7065#239 — same URI with/without trailing digest must not defect-fire."""
    uri = "cortex://notes/system/specs/closeout-plane-discrepancy-register.md"
    digest = "a" * 64
    with_digest = f"authored_cortex: {uri} {digest}"
    without_digest = f"authored_cortex: {uri}"
    assert checkpoint_dispositions_equivalent(with_digest, without_digest)
    assert (
        annotate_checkpoint_claim_discrepancy(
            claim=without_digest,
            measurement=f"authored_cortex@local-master: {uri} {digest}",
        )
        is None
    )


def test_checkpoint_dispositions_equivalent_committed_sha_prefix_and_pending() -> None:
    """7065#223 — short vs full SHA and pending prose normalize before compare."""
    full_sha = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
    short_sha = full_sha[:7]
    assert checkpoint_dispositions_equivalent(
        f"committed {short_sha} paths=1",
        f"committed@local-master {full_sha} paths=1",
    )
    assert checkpoint_dispositions_equivalent(
        f"committed {full_sha} paths=2 (+3 pending)",
        f"committed@local-master {full_sha} paths=2",
    )


def test_resolve_relay_status_does_not_prefer_section2_claim() -> None:
    """Step 2' — B2 must not re-derive from §2 claim (regression guard)."""
    body = "| status_claim | complete |\n"
    assert resolve_relay_status(body, "partial") == "partial"


def test_measurement_from_wrapper_uses_uncapped_machine_grade() -> None:
    """Step 2' — B2 source reads machine_status, not post-reconcile primary."""
    wrapper = json.dumps(
        {
            "schema_version": 1,
            "status": "partial",
            "capture_status": "ok",
            "status_authority_disagreement": {
                "authoritative": "machine_measurement",
                "machine_status": "complete",
                "authored_status": "partial",
            },
        }
    )
    assert resolve_measurement_status_from_wrapper(wrapper) == "complete"


def test_reconcile_preserves_machine_grade_when_authored_partial() -> None:
    """Step 5' — reconcile inverts; primary stays uncapped machine grade."""
    status, work_outcome, disagreement, deviations = reconcile_structured_with_authored(
        status=CloseoutStatus.COMPLETE,
        work_outcome=WorkOutcome.SHIPPED,
        sidecar_markdown="status_claim: partial\n",
    )
    assert status == CloseoutStatus.COMPLETE
    assert work_outcome == WorkOutcome.SHIPPED
    assert disagreement is not None
    assert disagreement["authoritative"] == "machine_measurement"
    assert disagreement["machine_status"] == "complete"
    assert disagreement["authored_status"] == "partial"
    assert disagreement["primary_status"] == "complete"
    assert "status_disagreement:authored_partial_vs_machine_complete" in deviations


def test_build_body_partial_claim_keeps_machine_complete_primary(
    tmp_path: Path,
) -> None:
    """Contaminated-agreement path structurally impossible under 2'/5' — primary is machine."""
    source_repo = tmp_path / "repo"
    cortex_root = tmp_path / "cortex"
    source_repo.mkdir()
    cortex_root.mkdir()
    offgit = ["cortex://notes/system/threads/fixture-deliverable.md"]
    rel = offgit[0].removeprefix("cortex://")
    path = cortex_root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# fixture\n", encoding="utf-8")

    section2 = """\
status_claim: partial — land green; live restart deferred

ac_verdict: partial met on land; live incomplete

deltas_to_spec: Done: fix. Not done: live verify.

decisions_taken: none

effects: land sha

evidence: pytest green

next: restart

open forks: none

access: ok

coverage: n/a

model_actual: cursor/grok-4.5

checkpoint_claim: committed deadbeef paths=1
"""
    body = build_implement_closeout_body(
        dispatch_id="7070-contaminated-specimen",
        outcome=SdkRunOutcome(
            body="executor prose",
            status="finished",
            duration_ms=100,
            tool_call_count=3,
        ),
        degraded_reason=None,
        sidecar_ref=sidecar_workspaces_ref("7070-contaminated-specimen"),
        result_bytes=200,
        thread_id="7070",
        work_item_ref="todo:closeout-status-claim-measurement",
        sidecar_markdown=section2,
        offgit_deliverable_uris=offgit,
        source_repo=source_repo,
        cortex_root=cortex_root,
        deliverables_expected=True,
    )
    payload = json.loads(body)
    assert payload["status"] == "complete"
    assert payload["work_outcome"] == "shipped"
    assert payload["status_authority_disagreement"]["machine_status"] == "complete"
    assert payload["status_authority_disagreement"]["authored_status"] == "partial"
    assert payload["status_authority_disagreement"]["authoritative"] == "machine_measurement"


def test_extract_status_claim_from_table_and_legacy_status() -> None:
    table_body = """\
| Field | Value |
|---|---|
| status_claim | complete |
"""
    assert extract_status_claim(table_body) == "complete"
    legacy = "status: partial\n"
    assert extract_status_claim(legacy) == "partial"


def test_call_order_reconcile_before_envelope_measurement() -> None:
    """file:line proof — reconcile inverts primary; B2 reads machine_status not capped primary."""
    reconcile_path = Path(
        "services/git_integration_worker/cursor_sdk_authored_status_reconcile.py"
    )
    closeout_path = Path(
        "services/git_integration_worker/cursor_auto/closeout_relay_common.py"
    )
    reconcile_text = reconcile_path.read_text(encoding="utf-8")
    common_text = closeout_path.read_text(encoding="utf-8")
    assert '"authoritative": "machine_measurement"' in reconcile_text
    assert "machine_status" in common_text
    assert "resolve_measurement_status_from_wrapper" in common_text
    assert "claim lives in §2" in common_text


_PARENT_RELOCATED_STUB_JSON = json.dumps(
    {
        "schema_version": 1,
        "status": "complete",
        "summary": "dispatch auto-91649020500f: nested leg",
        "work_outcome": "shipped",
        "body_relocated": {
            "uri": "cortex://notes/system/threads/6655-cursor-sdk-closeout-auto-91649020500f.md",
        },
    }
)


def _sidecar_with_structured_work_json() -> str:
    structured = json.dumps(
        {
            "schema_version": 1,
            "status": "partial",
            "status_incomplete_class": "work",
            "work_outcome": "checks_failed",
            "capture_status": "partial",
            "effects_manifest": {
                "dispatch_id": "65fb113d1438-64e49b12",
                "thread_id": "7165",
                "capture_sources": ["conversation"],
            },
        }
    )
    return (
        "## §2 closeout\n\n"
        "**status_claim:** complete\n\n"
        "**ac_verdict:** PASS\n\n"
        "**deltas_to_spec:** none\n\n"
        f"## structured_closeout_full\n\n{structured}"
    )


def test_select_closeout_relay_parent_stub_reads_structured_closeout_full_work() -> None:
    """6655#2652 live shape — parent ``body_relocated`` stub + sidecar work JSON."""
    from services.git_integration_worker.cursor_auto.closeout_relay import (
        select_closeout_relay_payload,
    )

    payload = select_closeout_relay_payload(
        sdk_body=_PARENT_RELOCATED_STUB_JSON,
        sidecar_text=_sidecar_with_structured_work_json(),
        ledger_status="completed",
        dispatch_id="auto-91649020500f",
    )
    assert payload.source == "section2_sidecar"
    assert payload.status == "partial:work"
