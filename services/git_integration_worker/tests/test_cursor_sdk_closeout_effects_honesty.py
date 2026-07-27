"""Off-git cortex URIs must appear in closeout effects (5528 t9 / §4.7)."""

from __future__ import annotations

import json

from services.git_integration_worker.cursor_sdk_capture_status import ChangeSet
from services.git_integration_worker.cursor_sdk_closeout import (
    SdkRunOutcome,
    build_implement_closeout_body,
)
from services.git_integration_worker.cursor_sdk_deliverables import (
    sidecar_workspaces_ref,
)

_T9_OFFGIT_A = "cortex://notes/system/specs/operator-proxy-closeout-section2-relay.md"
_T9_OFFGIT_B = (
    "cortex://notes/system/reviews/closeout-honesty-spec-review-grok-2026-07-26.md"
)


def _outcome() -> SdkRunOutcome:
    return SdkRunOutcome(
        body="done",
        status="finished",
        duration_ms=100,
        tool_call_count=2,
    )


def test_effects_names_offgit_uris_when_repo_change_set_empty() -> None:
    body = build_implement_closeout_body(
        dispatch_id="effects-offgit-t9",
        outcome=_outcome(),
        degraded_reason=None,
        sidecar_ref=sidecar_workspaces_ref("effects-offgit-t9"),
        result_bytes=4,
        thread_id="t-effects-offgit",
        work_item_ref="todo:operator-proxy-closeout-section2-relay",
        change_set=ChangeSet(created=(), modified=(), deleted=()),
        offgit_deliverable_uris=[_T9_OFFGIT_A, _T9_OFFGIT_B],
    )
    payload = json.loads(body)
    assert payload["effects"]
    assert _T9_OFFGIT_A in payload["effects"]
    assert _T9_OFFGIT_B in payload["effects"]
    assert payload["files_created"] == []


def test_effects_preserves_repo_paths_before_offgit() -> None:
    repo_path = "services/git_integration_worker/cursor_auto/handler.py"
    body = build_implement_closeout_body(
        dispatch_id="effects-order",
        outcome=_outcome(),
        degraded_reason=None,
        sidecar_ref=sidecar_workspaces_ref("effects-order"),
        result_bytes=4,
        thread_id="t-effects-order",
        work_item_ref="todo:effects-order",
        change_set=ChangeSet(created=(), modified=(repo_path,), deleted=()),
        offgit_deliverable_uris=[_T9_OFFGIT_A],
    )
    payload = json.loads(body)
    effects = payload["effects"]
    assert effects.index(repo_path) < effects.index(_T9_OFFGIT_A)
