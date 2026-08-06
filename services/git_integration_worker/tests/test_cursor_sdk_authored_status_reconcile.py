"""Row 20 — §2 partial must not become structured shipped/complete."""

from __future__ import annotations

import json
from pathlib import Path

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

_PARTIAL_SECTION2 = """## Closeout

### §2

status: partial — land green; live restart deferred

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

checkpoint: committed deadbeef paths=1
"""


def test_reconcile_caps_shipped_when_section2_partial() -> None:
    status, work_outcome, disagreement, deviations = (
        reconcile_structured_with_authored(
            status=CloseoutStatus.COMPLETE,
            work_outcome=WorkOutcome.SHIPPED,
            sidecar_markdown=_PARTIAL_SECTION2,
        )
    )
    assert status == CloseoutStatus.PARTIAL
    assert work_outcome == WorkOutcome.NOT_SHIPPED
    assert disagreement is not None
    assert disagreement["authored_status"] == "partial"
    assert disagreement["machine_status"] == "complete"
    assert disagreement["machine_work_outcome"] == "shipped"
    assert disagreement["authoritative"] == "authored_section2"
    assert "status_disagreement:authored_partial_vs_machine_complete" in deviations


def test_reconcile_noop_without_section2() -> None:
    status, work_outcome, disagreement, deviations = (
        reconcile_structured_with_authored(
            status=CloseoutStatus.COMPLETE,
            work_outcome=WorkOutcome.SHIPPED,
            sidecar_markdown="# no section2 status\n",
        )
    )
    assert status == CloseoutStatus.COMPLETE
    assert work_outcome == WorkOutcome.SHIPPED
    assert disagreement is None
    assert deviations == []


def test_build_body_partial_section2_does_not_ship(
    tmp_path: Path,
) -> None:
    """AC: §2 status:partial ⇒ structured status/work_outcome ¬ complete/shipped."""
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
    )
    payload = json.loads(body)
    assert payload["status"] == "partial"
    assert payload["work_outcome"] == "not_shipped"
    assert payload["status_authority_disagreement"]["machine_work_outcome"] == "shipped"
    assert payload["status_authority_disagreement"]["authored_status"] == "partial"
    assert (
        "status_disagreement:authored_partial_vs_machine_complete"
        in payload["deviations"]
    )
