"""Read-coverage gate + ``partial:coverage`` classifier ordering (fork B)."""

from __future__ import annotations

import json

from implement_admission.closeout_models import (
    EffectEntry,
    EffectsManifest,
    ImplementCloseout,
    ReadInventoryRow,
    SurfaceSection,
)
from implement_admission.normalize import required_reads_from_packet
from implement_admission.read_coverage import build_read_inventory
from implement_admission.spec import CloseoutStatus, WorkOutcome

from services.git_integration_worker.cursor_sdk_capture_status import (
    apply_read_coverage_gate,
)
from services.git_integration_worker.cursor_sdk_closeout import (
    SdkRunOutcome,
    build_implement_closeout_body,
)
from services.git_integration_worker.cursor_sdk_deliverables import (
    sidecar_workspaces_ref,
)
from services.git_integration_worker.relay.closeout_status_polarity import (
    classify_status_incomplete_class,
    measurement_incomplete_class,
    merge_plane_discrepancy_markers,
    qualify_partial_measurement,
    resolve_qualified_measurement_status,
    status_claim_is_polysemous_partial_legend,
)


def _manifest_with_repo_read(path: str) -> EffectsManifest:
    return EffectsManifest(
        dispatch_id="d-read",
        thread_id="t-read",
        capture_sources=["stream"],
        surfaces={
            "repo": SurfaceSection(
                surface="repo",
                source="stream",
                entries=[EffectEntry(op="observed", target=path, identity=path)],
            )
        },
    )


def test_required_reads_from_packet_block_list() -> None:
    text = "required_reads:\n- libs/a.py\n- libs/b.py\n"
    assert required_reads_from_packet(text) == ["libs/a.py", "libs/b.py"]


def test_apply_read_coverage_gate_downgrades_without_manifest_read() -> None:
    inv = [ReadInventoryRow(path="libs/a.py", observed=False, source="seat")]
    status, devs = apply_read_coverage_gate(
        status=CloseoutStatus.COMPLETE,
        required_reads=["libs/a.py"],
        read_inventory=inv,
        deviations=None,
    )
    assert status == CloseoutStatus.PARTIAL
    assert "coverage:required_reads_unobserved:1" in devs


def test_apply_read_coverage_gate_noop_when_empty_required_reads() -> None:
    status, devs = apply_read_coverage_gate(
        status=CloseoutStatus.COMPLETE,
        required_reads=[],
        read_inventory=[],
        deviations=["capture:foo"],
    )
    assert status == CloseoutStatus.COMPLETE
    assert devs == ["capture:foo"]


def test_apply_read_coverage_gate_observed_leaves_complete() -> None:
    manifest = _manifest_with_repo_read("libs/a.py")
    inv = build_read_inventory(["libs/a.py"], manifest)
    status, devs = apply_read_coverage_gate(
        status=CloseoutStatus.COMPLETE,
        required_reads=["libs/a.py"],
        read_inventory=inv,
        deviations=[],
    )
    assert status == CloseoutStatus.COMPLETE
    assert not any(d.startswith("coverage:") for d in devs)


def test_build_body_read_coverage_even_when_deliverables_not_expected(
    tmp_path,
) -> None:
    source_repo = tmp_path / "repo"
    cortex_root = tmp_path / "cortex"
    source_repo.mkdir()
    cortex_root.mkdir()
    body = build_implement_closeout_body(
        dispatch_id="cov-gate-d1",
        outcome=SdkRunOutcome(
            body="done",
            status="finished",
            duration_ms=50,
            tool_call_count=1,
        ),
        degraded_reason=None,
        sidecar_ref=sidecar_workspaces_ref("cov-gate-d1"),
        result_bytes=100,
        thread_id="23977",
        work_item_ref="todo:read-cov",
        packet_text="required_reads:\n- libs/missing.py\n",
        effects_manifest=EffectsManifest(dispatch_id="cov-gate-d1", thread_id="23977"),
        source_repo=source_repo,
        cortex_root=cortex_root,
        deliverables_expected=False,
        sidecar_markdown="status: complete\n",
    )
    payload = json.loads(body)
    assert payload["status"] == "partial"
    assert payload["status_incomplete_class"] == "coverage"
    assert payload["required_reads"] == ["libs/missing.py"]
    assert payload["read_inventory"][0]["observed"] is False


def test_classifier_work_before_coverage_before_capture() -> None:
    work = classify_status_incomplete_class(
        status=CloseoutStatus.PARTIAL,
        work_outcome=WorkOutcome.NOT_SHIPPED,
        capture_status="partial",
        escalation_harvest="none",
        deviations=["land:lane_b_unlanded", "coverage:required_reads_unobserved:1"],
    )
    assert work == "work"
    coverage = classify_status_incomplete_class(
        status=CloseoutStatus.PARTIAL,
        work_outcome=WorkOutcome.SHIPPED,
        capture_status="partial",
        escalation_harvest="none",
        deviations=["coverage:required_reads_unobserved:1", "capture:shell"],
    )
    assert coverage == "coverage"
    capture = classify_status_incomplete_class(
        status=CloseoutStatus.PARTIAL,
        work_outcome=WorkOutcome.SHIPPED,
        capture_status="partial",
        escalation_harvest="none",
        deviations=["capture:shell"],
    )
    assert capture == "capture"


def test_partial_coverage_measurement_and_plane_discrepancy() -> None:
    wrapper = json.dumps(
        {
            "schema_version": 1,
            "status": "partial",
            "status_incomplete_class": "coverage",
        }
    )
    assert resolve_qualified_measurement_status(
        base_status="partial",
        wrapper_text=wrapper,
    ) == "partial:coverage"
    assert measurement_incomplete_class("partial:coverage") == "coverage"
    assert qualify_partial_measurement("partial", "coverage") == "partial:coverage"
    marker = "status_claim@§2 complete while status@infra partial:coverage"
    assert merge_plane_discrepancy_markers(marker) is not None
    assert (
        status_claim_is_polysemous_partial_legend(
            claim="complete",
            measurement="partial:coverage",
        )
        is False
    )


def test_historical_closeout_json_without_read_fields() -> None:
    raw = {
        "schema_version": 1,
        "status": "complete",
        "summary": "legacy",
        "source_ref": "todo:legacy",
    }
    model = ImplementCloseout.model_validate(raw)
    assert model.required_reads == []
    assert model.read_inventory == []
