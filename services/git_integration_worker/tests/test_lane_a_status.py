"""Lane-A status_claim vs envelope measurement — arc 7070 specimen tests."""

from __future__ import annotations

import json
from pathlib import Path

from implement_admission.spec import CloseoutStatus, WorkOutcome

from services.git_integration_worker.cursor_auto.closeout_plane_probe import (
    annotate_status_claim_discrepancy,
    merge_plane_discrepancy_markers,
    status_dispositions_equivalent,
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
    """Contaminated agreement — claim partial vs uncapped machine complete must fire."""
    marker = annotate_status_claim_discrepancy(
        claim="partial",
        measurement="complete",
    )
    assert marker == "status_claim@§2 partial while status@infra complete"
    merged = merge_plane_discrepancy_markers(marker)
    assert merged == "plane-discrepancy: status_claim@§2 partial while status@infra complete"


def test_specimen_independent_disagreement_fires() -> None:
    """Independent disagreement — claim complete vs machine partial fires."""
    marker = annotate_status_claim_discrepancy(
        claim="complete",
        measurement="partial",
    )
    assert marker == "status_claim@§2 complete while status@infra partial"


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
