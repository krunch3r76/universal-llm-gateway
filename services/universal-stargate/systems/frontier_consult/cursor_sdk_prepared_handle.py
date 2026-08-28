"""Prepared cursor-sdk handle type and serialization."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from typing import Any, Literal


@dataclass(frozen=True, slots=True)
class PreparedCursorSdkHandle:
    """Stable admission identity + fingerprint-bearing worker request fields."""

    request_id: str
    execution_id: str
    dispatch_id: str
    thread_id: str | None
    resolved_model: str
    role: str
    family: str
    platform: str
    to_agent: str
    handoff_contract: str
    packet_path: str | None
    message: str | None
    caller_agent: str | None
    read_only: bool
    aligned_knobs: dict[str, str] | None
    prompt_preamble: str | None
    thread_subject: str
    pointer_body: str
    effective_bus_lifecycle: Literal["persistent", "ephemeral"]
    parent_dispatch_thread_id: str | None
    dispatch_thread_id: str | None
    density_triage: str | None
    review_opt_out_reason_code: str | None
    auto_review_child: bool
    auto_review_defaulted: bool
    claimed_via_atomic: bool
    admitted: bool
    alignment_warnings: tuple[dict[str, Any], ...]
    knob_resolution: tuple[dict[str, Any], ...]
    nest_under: str | None = None
    lane: Literal["A", "B"] | None = None
    workspace: str | None = None
    refuse_if_lease_held: bool = False
    prompt_turn_number: int | None = None
    prompt_bind_mode: str | None = None
    poll_after_turn: int | None = None


def mint_cursor_sdk_ids(*, request_id: str) -> tuple[str, str]:
    """Pure mint: execution_id + worker dispatch_id (no I/O)."""
    execution_id = str(uuid.uuid4())
    dispatch_id = f"{request_id}-{uuid.uuid4().hex[:8]}"
    return execution_id, dispatch_id


def handle_with_thread(
    handle: PreparedCursorSdkHandle, *, thread_id: str
) -> PreparedCursorSdkHandle:
    """Return a copy with thread_id set (idempotent materialization helper)."""
    if handle.thread_id == thread_id:
        return handle
    return replace(handle, thread_id=thread_id)


def handle_to_dict(handle: PreparedCursorSdkHandle) -> dict[str, Any]:
    """Serialize handle for proposal-store checkpointing."""
    return {
        "request_id": handle.request_id,
        "execution_id": handle.execution_id,
        "dispatch_id": handle.dispatch_id,
        "thread_id": handle.thread_id,
        "resolved_model": handle.resolved_model,
        "role": handle.role,
        "family": handle.family,
        "platform": handle.platform,
        "to_agent": handle.to_agent,
        "handoff_contract": handle.handoff_contract,
        "packet_path": handle.packet_path,
        "message": handle.message,
        "caller_agent": handle.caller_agent,
        "read_only": handle.read_only,
        "aligned_knobs": handle.aligned_knobs,
        "prompt_preamble": handle.prompt_preamble,
        "thread_subject": handle.thread_subject,
        "pointer_body": handle.pointer_body,
        "effective_bus_lifecycle": handle.effective_bus_lifecycle,
        "parent_dispatch_thread_id": handle.parent_dispatch_thread_id,
        "dispatch_thread_id": handle.dispatch_thread_id,
        "density_triage": handle.density_triage,
        "review_opt_out_reason_code": handle.review_opt_out_reason_code,
        "auto_review_child": handle.auto_review_child,
        "auto_review_defaulted": handle.auto_review_defaulted,
        "claimed_via_atomic": handle.claimed_via_atomic,
        "admitted": handle.admitted,
        "alignment_warnings": list(handle.alignment_warnings),
        "knob_resolution": list(handle.knob_resolution),
        "nest_under": handle.nest_under,
        "lane": handle.lane,
        "workspace": handle.workspace,
        "refuse_if_lease_held": handle.refuse_if_lease_held,
        "prompt_turn_number": handle.prompt_turn_number,
        "prompt_bind_mode": handle.prompt_bind_mode,
        "poll_after_turn": handle.poll_after_turn,
    }


def handle_from_dict(data: dict[str, Any]) -> PreparedCursorSdkHandle:
    """Deserialize a checkpointed handle."""
    return PreparedCursorSdkHandle(
        request_id=str(data["request_id"]),
        execution_id=str(data["execution_id"]),
        dispatch_id=str(data["dispatch_id"]),
        thread_id=None if data.get("thread_id") is None else str(data["thread_id"]),
        resolved_model=str(data["resolved_model"]),
        role=str(data["role"]),
        family=str(data["family"]),
        platform=str(data["platform"]),
        to_agent=str(data["to_agent"]),
        handoff_contract=str(data["handoff_contract"]),
        packet_path=data.get("packet_path"),
        message=data.get("message"),
        caller_agent=data.get("caller_agent"),
        read_only=bool(data.get("read_only", False)),
        aligned_knobs=data.get("aligned_knobs"),
        prompt_preamble=data.get("prompt_preamble"),
        thread_subject=str(data["thread_subject"]),
        pointer_body=str(data["pointer_body"]),
        effective_bus_lifecycle=data.get("effective_bus_lifecycle") or "ephemeral",
        parent_dispatch_thread_id=data.get("parent_dispatch_thread_id"),
        dispatch_thread_id=data.get("dispatch_thread_id"),
        density_triage=data.get("density_triage"),
        review_opt_out_reason_code=data.get("review_opt_out_reason_code"),
        auto_review_child=bool(data.get("auto_review_child", False)),
        auto_review_defaulted=bool(data.get("auto_review_defaulted", False)),
        claimed_via_atomic=bool(data.get("claimed_via_atomic", False)),
        admitted=bool(data.get("admitted", False)),
        alignment_warnings=tuple(data.get("alignment_warnings") or ()),
        knob_resolution=tuple(data.get("knob_resolution") or ()),
        nest_under=data.get("nest_under"),
        lane=data.get("lane"),
        workspace=data.get("workspace"),
        refuse_if_lease_held=bool(data.get("refuse_if_lease_held", False)),
        prompt_turn_number=data.get("prompt_turn_number"),
        prompt_bind_mode=data.get("prompt_bind_mode"),
        poll_after_turn=data.get("poll_after_turn"),
    )
