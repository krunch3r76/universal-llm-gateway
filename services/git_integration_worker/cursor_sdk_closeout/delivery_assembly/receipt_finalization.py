"""Usage/appendix sidecar suffix, structured-full receipt persistence, oversize relocation, CloseoutDelivery construction.

Mutates the sidecar file after ``build_implement_closeout_body`` may have
appended the full manifest JSON onto ``sidecar_appendix`` — that list must be
the same object. Sync oversize relocation stays here; the async twin is
``relocate_oversize_delivery_async``.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from implement_admission.spec import CloseoutStatus

from services.git_integration_worker.cursor_sdk_branch_terminal import (
    settle_lane_branch,
)
from services.git_integration_worker.cursor_sdk_capture_status import ChangeSet
from services.git_integration_worker.cursor_sdk_deliverables import (
    persist_structured_closeout_full_to_repo_sidecar,
    relocate_oversize_closeout_body_async,
    relocate_oversize_closeout_body_sync,
)
from services.git_integration_worker.cursor_sdk_events import (
    emit_sdk_closeout_relocated,
)
from services.git_integration_worker.cursor_sdk_usage_sidecar import (
    render_usage_sidecar_section,
)

from ..bus_body_budget import MAX_TURN_BODY_CHARS, finalize_closeout_body
from ..closeout_records import CloseoutDelivery, SdkRunOutcome


def finalize_closeout_receipt(
    *,
    source_repo: Path,
    lane_b_branch: str | None,
    thread_id: str,
    dispatch_id: str,
    text: str,
    capture_commits_ahead: int | None,
    capture_landed: bool | None,
    capture_head_sha: str | None,
    repo_change_set: ChangeSet,
    outcome: SdkRunOutcome,
    resolved_model: str | None,
    sidecar_appendix: list[str],
    sidecar_path: Path,
    result_bytes: int,
    body: str,
    sidecar_ref: str,
    execution_id: str,
    finalize_oversize: bool,
    post_closeout_sidecar_fn: Callable[..., dict[str, Any] | None] | None,
    read_only: bool | None = None,
) -> CloseoutDelivery:
    """Write sidecar suffix + structured receipt; settle the lane branch if owed.

    *read_only* skips ``settle_lane_branch`` so an advisory leg cannot fake
    Lane-B land state. ``None`` looks the flag up from the dispatch ledger.
    """
    if read_only is None:
        from services.git_integration_worker.cursor_dispatch_ledger import (
            CursorDispatchLedger,
        )

        read_only = CursorDispatchLedger.instance().read_read_only(
            dispatch_id=dispatch_id
        )
    # Read-only legs never own a Lane-B branch. Settling them fakes land state
    # (debt / unlanded grade) for an advisory dispatch. Skip even when a
    # branch name leaked onto the closeout fields.
    if not read_only:
        settle_lane_branch(
            source_repo=source_repo,
            branch_name=lane_b_branch,
            thread_id=thread_id,
            dispatch_id=dispatch_id,
            closeout_text=text,
            commits_ahead=capture_commits_ahead,
            landed=capture_landed,
            head_sha=capture_head_sha,
            files=[*repo_change_set.created, *repo_change_set.modified],
        )
    usage_section = render_usage_sidecar_section(
        usage=outcome.usage,
        usage_capture_status=outcome.usage_capture_status,
        resolved_model=resolved_model,
    )
    sidecar_suffix_parts: list[str] = []
    if sidecar_appendix:
        sidecar_suffix_parts.append(
            "\n\n## effects_manifest\n\n" + "\n".join(sidecar_appendix)
        )
    if usage_section:
        sidecar_suffix_parts.append("\n\n" + usage_section)
    if sidecar_suffix_parts:
        sidecar_path.write_text(
            sidecar_path.read_text(encoding="utf-8") + "".join(sidecar_suffix_parts),
            encoding="utf-8",
        )
        result_bytes = len(sidecar_path.read_text(encoding="utf-8").encode("utf-8"))
    full_body = body
    receipt_deviation = persist_structured_closeout_full_to_repo_sidecar(
        sidecar_path=sidecar_path,
        full_body=full_body,
        dispatch_id=dispatch_id,
        thread_id=thread_id,
    )
    if receipt_deviation:
        payload = json.loads(full_body)
        payload_deviations = list(payload.get("deviations") or [])
        if receipt_deviation not in payload_deviations:
            payload_deviations.append(receipt_deviation)
        payload["deviations"] = payload_deviations
        if payload.get("status") != "failed":
            payload["capture_status"] = "partial"
        full_body = json.dumps(payload, separators=(",", ":"))
        body = full_body
    if len(full_body) > MAX_TURN_BODY_CHARS and finalize_oversize:
        body_relocated, tier = relocate_oversize_closeout_body_sync(
            full_body=full_body,
            sidecar_path=sidecar_path,
            sidecar_ref=sidecar_ref,
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            post_closeout_sidecar_fn=post_closeout_sidecar_fn,
        )
        emit_sdk_closeout_relocated(
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            execution_id=execution_id,
            uri=body_relocated["uri"],
            body_chars=body_relocated["body_chars"],
            tier=tier,
        )
        body = finalize_closeout_body(full_body, body_relocated=body_relocated)
    parsed = json.loads(body)
    return CloseoutDelivery(
        body=body,
        sidecar_ref=sidecar_ref,
        sidecar_path=sidecar_path,
        full_result_bytes=result_bytes,
        closeout_status=CloseoutStatus(parsed["status"]),
    )


async def relocate_oversize_delivery_async(
    delivery: CloseoutDelivery,
    *,
    dispatch_id: str,
    thread_id: str,
    execution_id: str,
    post_closeout_sidecar_fn: Callable[..., Any] | None,
) -> CloseoutDelivery:
    """Move an oversize closeout body off the bus turn and return the relocated delivery."""
    full_body = delivery.body
    if len(full_body) <= MAX_TURN_BODY_CHARS:
        return delivery
    body_relocated, tier = await relocate_oversize_closeout_body_async(
        full_body=full_body,
        sidecar_path=delivery.sidecar_path,
        sidecar_ref=delivery.sidecar_ref,
        dispatch_id=dispatch_id,
        thread_id=thread_id,
        post_closeout_sidecar_fn=post_closeout_sidecar_fn,
    )
    emit_sdk_closeout_relocated(
        dispatch_id=dispatch_id,
        thread_id=thread_id,
        execution_id=execution_id,
        uri=body_relocated["uri"],
        body_chars=body_relocated["body_chars"],
        tier=tier,
    )
    body = finalize_closeout_body(full_body, body_relocated=body_relocated)
    parsed = json.loads(body)
    return CloseoutDelivery(
        body=body,
        sidecar_ref=delivery.sidecar_ref,
        sidecar_path=delivery.sidecar_path,
        full_result_bytes=delivery.full_result_bytes,
        closeout_status=CloseoutStatus(parsed["status"]),
    )
