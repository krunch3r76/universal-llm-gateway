"""Direct cursor-sdk closeout must carry propagation_residue (friction 26340)."""

from __future__ import annotations

import json

from services.git_integration_worker.cursor_sdk_capture_status import ChangeSet
from services.git_integration_worker.cursor_sdk_closeout import (
    SdkRunOutcome,
    build_implement_closeout_body,
    finalize_closeout_body,
)
from services.git_integration_worker.cursor_sdk_deliverables import (
    sidecar_workspaces_ref,
)


def _outcome() -> SdkRunOutcome:
    return SdkRunOutcome(
        body="done",
        status="finished",
        duration_ms=100,
        tool_call_count=2,
    )


def test_build_closeout_includes_sync_restart_residue() -> None:
    body = build_implement_closeout_body(
        dispatch_id="residue-giw",
        outcome=_outcome(),
        degraded_reason=None,
        sidecar_ref=sidecar_workspaces_ref("residue-giw"),
        result_bytes=4,
        thread_id="t-residue",
        work_item_ref="todo:residue",
        change_set=ChangeSet(
            created=(),
            modified=("services/git_integration_worker/cursor_auto/handler.py",),
            deleted=(),
        ),
    )
    payload = json.loads(body)
    assert any(
        line.startswith("sync_restart: git_integration_worker")
        for line in payload["propagation_residue"]
    )


def test_build_closeout_includes_plugin_install_residue() -> None:
    body = build_implement_closeout_body(
        dispatch_id="residue-plugin",
        outcome=_outcome(),
        degraded_reason=None,
        sidecar_ref=sidecar_workspaces_ref("residue-plugin"),
        result_bytes=4,
        thread_id="t-residue-plugin",
        work_item_ref=None,
        change_set=ChangeSet(
            created=(),
            modified=(
                "cursor-plugins/ulg-ecosystem/rules/mcp-tool-awareness_ulg.mdc",
            ),
            deleted=(),
        ),
    )
    payload = json.loads(body)
    assert any(
        line.startswith("install_plugin:") for line in payload["propagation_residue"]
    )


def test_build_closeout_empty_residue_for_docs_only() -> None:
    body = build_implement_closeout_body(
        dispatch_id="residue-docs",
        outcome=_outcome(),
        degraded_reason=None,
        sidecar_ref=sidecar_workspaces_ref("residue-docs"),
        result_bytes=4,
        thread_id="t-residue-docs",
        work_item_ref=None,
        change_set=ChangeSet(
            created=(),
            modified=("docs/architecture/overview.md",),
            deleted=(),
        ),
    )
    payload = json.loads(body)
    assert payload["propagation_residue"] == []


def test_finalize_preserves_propagation_residue() -> None:
    body = build_implement_closeout_body(
        dispatch_id="residue-finalize",
        outcome=_outcome(),
        degraded_reason=None,
        sidecar_ref=sidecar_workspaces_ref("residue-finalize"),
        result_bytes=4,
        thread_id="t-residue-finalize",
        work_item_ref="todo:residue-finalize",
        change_set=ChangeSet(
            created=(),
            modified=("services/git_integration_worker/x.py",),
            deleted=(),
        ),
    )
    # Force shrink path by bloating summary via raw re-encode is awkward;
    # assert the helper keeps the field when already under limit and when present.
    payload = json.loads(body)
    assert payload["propagation_residue"]
    finalized = finalize_closeout_body(body)
    assert json.loads(finalized)["propagation_residue"] == payload["propagation_residue"]
