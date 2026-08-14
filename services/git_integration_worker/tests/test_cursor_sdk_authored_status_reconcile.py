"""Row 20 — §2 partial/absent records disagreement without capping machine grade."""

from __future__ import annotations

import json
from pathlib import Path

from implement_admission.closeout_models import observed_process_verification
from implement_admission.spec import CloseoutStatus, WorkOutcome

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
from services.git_integration_worker.cursor_sdk_stream_capture import (
    ToolCallObservation,
)


def _passing_pytest_obs() -> ToolCallObservation:
    result = {
        "status": "success",
        "value": {"stdout": "1 passed", "stderr": "", "exitCode": 0},
    }
    return ToolCallObservation(
        call_id="call-pytest-pass",
        tool_name="shell",
        status="completed",
        arg_bytes=1,
        result_bytes=1,
        truncated_fields=(),
        args={"command": "pytest -q foo.py"},
        result=result,
        result_body=result,
        result_body_status="present",
    )

_PARTIAL_SECTION2 = """## Closeout

### §2

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

model_actual: cursor/grok-4.6

checkpoint_claim: committed deadbeef paths=1
"""


def test_reconcile_records_disagreement_when_section2_partial() -> None:
    status, work_outcome, disagreement, deviations = (
        reconcile_structured_with_authored(
            status=CloseoutStatus.COMPLETE,
            work_outcome=WorkOutcome.SHIPPED,
            sidecar_markdown=_PARTIAL_SECTION2,
        )
    )
    assert status == CloseoutStatus.COMPLETE
    assert work_outcome == WorkOutcome.SHIPPED
    assert disagreement is not None
    assert disagreement["authored_status"] == "partial"
    assert disagreement["machine_status"] == "complete"
    assert disagreement["machine_work_outcome"] == "shipped"
    assert disagreement["authoritative"] == "machine_measurement"
    assert "status_disagreement:authored_partial_vs_machine_complete" in deviations


def test_reconcile_records_disagreement_when_section2_absent_no_deliverables() -> None:
    """Absent §2 on deliverables_expected=False must record disagreement, not cap."""
    status, work_outcome, disagreement, deviations = (
        reconcile_structured_with_authored(
            status=CloseoutStatus.COMPLETE,
            work_outcome=WorkOutcome.SHIPPED,
            sidecar_markdown="# no section2 status\n",
            deliverables_expected=False,
        )
    )
    assert status == CloseoutStatus.COMPLETE
    assert work_outcome == WorkOutcome.SHIPPED
    assert disagreement is not None
    assert disagreement["authored_status"] is None
    assert disagreement["machine_status"] == "complete"
    assert disagreement["machine_work_outcome"] == "shipped"
    assert "status_disagreement:authored_absent_vs_machine_complete" in deviations


def test_reconcile_records_when_sidecar_markdown_empty_no_deliverables() -> None:
    status, work_outcome, disagreement, deviations = (
        reconcile_structured_with_authored(
            status=CloseoutStatus.COMPLETE,
            work_outcome=WorkOutcome.SHIPPED,
            sidecar_markdown=None,
            deliverables_expected=False,
        )
    )
    assert status == CloseoutStatus.COMPLETE
    assert work_outcome == WorkOutcome.SHIPPED
    assert disagreement is not None
    assert disagreement["authored_status"] is None
    assert "status_disagreement:authored_absent_vs_machine_complete" in deviations


def test_reconcile_absent_noop_when_deliverables_expected() -> None:
    """Evidence-backed implement path: absent §2 does not record disagreement."""
    status, work_outcome, disagreement, deviations = (
        reconcile_structured_with_authored(
            status=CloseoutStatus.COMPLETE,
            work_outcome=WorkOutcome.SHIPPED,
            sidecar_markdown="# no section2 status\n",
            deliverables_expected=True,
        )
    )
    assert status == CloseoutStatus.COMPLETE
    assert work_outcome == WorkOutcome.SHIPPED
    assert disagreement is None
    assert deviations == []


def test_reconcile_noop_when_section2_complete() -> None:
    status, work_outcome, disagreement, deviations = (
        reconcile_structured_with_authored(
            status=CloseoutStatus.COMPLETE,
            work_outcome=WorkOutcome.SHIPPED,
            sidecar_markdown="status_claim: complete — done\n",
            deliverables_expected=False,
        )
    )
    assert status == CloseoutStatus.COMPLETE
    assert work_outcome == WorkOutcome.SHIPPED
    assert disagreement is None
    assert deviations == []


_COMPLETE_SECTION2_PAPER_OVER = """## §2

status_claim: complete

ac_verdict: all 6 AC members pass (table below)
"""


def test_reconcile_authored_complete_does_not_upgrade_checks_failed() -> None:
    """auto-32296c7fe474 class — §2 complete must not launder observed check failure."""
    status, work_outcome, disagreement, deviations = (
        reconcile_structured_with_authored(
            status=CloseoutStatus.PARTIAL,
            work_outcome=WorkOutcome.CHECKS_FAILED,
            sidecar_markdown=_COMPLETE_SECTION2_PAPER_OVER,
            deliverables_expected=True,
        )
    )
    assert status == CloseoutStatus.PARTIAL
    assert work_outcome == WorkOutcome.CHECKS_FAILED
    assert disagreement is None
    assert deviations == []


def test_build_body_authored_complete_keeps_observed_ruff_fail_checks_failed() -> None:
    """Specimen auto-32296c7fe474: observed ruff exit 1 + §2 complete stays checks_failed."""
    ruff_stdout = (
        "I001 [*] Import block is un-sorted or un-formatted\n"
        "  --> libs/implement_admission/test_check_review_substrate_default.py:3:1\n"
        "Found 4 errors.\n"
    )
    verification = [
        observed_process_verification(
            command="ruff check 8 touched files",
            exit_code=1,
            invocation_id="lint:ce70f1e4cdfb49b3bcec4a96a81216e9",
            basis="subprocess.run.returncode",
            stdout=ruff_stdout,
            executable="/home/io/.venvs/universal/bin/ruff",
            tool_version="0.15.6",
        )
    ]
    body = build_implement_closeout_body(
        dispatch_id="auto-32296c7fe474",
        outcome=SdkRunOutcome(
            body="status_claim: complete\nac_verdict: all 6 AC members pass",
            status="finished",
            duration_ms=505400,
            tool_call_count=145,
        ),
        degraded_reason=None,
        sidecar_ref=sidecar_workspaces_ref("auto-32296c7fe474"),
        result_bytes=7497,
        thread_id="7217",
        work_item_ref="todo:closeout-envelope-section2-honesty",
        sidecar_markdown=_COMPLETE_SECTION2_PAPER_OVER,
        verification=verification,
        deliverables_expected=True,
    )
    payload = json.loads(body)
    assert payload["work_outcome"] == WorkOutcome.CHECKS_FAILED.value
    assert payload["status"] == CloseoutStatus.PARTIAL.value
    assert payload.get("status_authority_disagreement") is None


def test_build_body_partial_section2_preserves_machine_grade(
    tmp_path: Path,
) -> None:
    """§2 status_claim:partial ⇒ structured status stays machine complete/shipped."""
    source_repo = tmp_path / "repo"
    cortex_root = tmp_path / "cortex"
    source_repo.mkdir()
    cortex_root.mkdir()
    offgit = [
        "cortex://notes/system/threads/cursor-sdk-feature-alignment/"
        "row20-fixture-deliverable.md",
    ]
    rel = offgit[0].removeprefix("cortex://")
    path = cortex_root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# fixture\n", encoding="utf-8")

    body = build_implement_closeout_body(
        dispatch_id="row20-partial-vs-shipped",
        outcome=SdkRunOutcome(
            body="executor prose with section2 partial",
            status="finished",
            duration_ms=100,
            tool_call_count=3,
            tool_calls=(_passing_pytest_obs(),),
        ),
        degraded_reason=None,
        sidecar_ref=sidecar_workspaces_ref("row20-partial-vs-shipped"),
        result_bytes=200,
        thread_id="6655",
        work_item_ref="todo:row-20-relay-status-upgrade",
        sidecar_markdown=_PARTIAL_SECTION2,
        offgit_deliverable_uris=offgit,
        source_repo=source_repo,
        cortex_root=cortex_root,
        deliverables_expected=True,
        verification=[
            observed_process_verification(
                command="pytest -q foo.py",
                exit_code=0,
                invocation_id="test:row20",
            )
        ],
    )
    payload = json.loads(body)
    assert payload["status"] == "complete"
    assert payload["work_outcome"] == "shipped"
    assert payload["status_authority_disagreement"]["machine_work_outcome"] == "shipped"
    assert payload["status_authority_disagreement"]["authored_status"] == "partial"
    assert (
        "status_disagreement:authored_partial_vs_machine_complete"
        in payload["deviations"]
    )


def test_build_body_auto40eacccc1b48_absent_section2_records_disagreement(
    tmp_path: Path,
) -> None:
    """Specimen auto-40eacccc1b48: no §2, empty verification, investigate."""
    source_repo = tmp_path / "repo"
    cortex_root = tmp_path / "cortex"
    source_repo.mkdir()
    cortex_root.mkdir()
    narrative = (
        "I'll seed row 20 as investigate+fix at G1, then recon the "
        "closeout relay path for where work_outcome: shipped is produced.\n"
    )
    body = build_implement_closeout_body(
        dispatch_id="auto-40eacccc1b48",
        outcome=SdkRunOutcome(
            body=narrative,
            status="finished",
            duration_ms=37300,
            tool_call_count=8,
        ),
        degraded_reason=None,
        sidecar_ref=sidecar_workspaces_ref("auto-40eacccc1b48"),
        result_bytes=9050,
        thread_id="6655",
        work_item_ref=None,
        verification=[],
        deviations=[
            "capture:outside_repo_paths_present",
            "stream_only_effect",
            "degraded:sdk_git_probe_absent",
        ],
        sidecar_markdown=narrative,
        source_repo=source_repo,
        cortex_root=cortex_root,
        deliverables_expected=False,
        landed=None,
        isolation_materialized=True,
    )
    payload = json.loads(body)
    assert payload["status"] == "partial"
    assert payload["work_outcome"] == "unverified"
    assert payload.get("status_authority_disagreement") is None
