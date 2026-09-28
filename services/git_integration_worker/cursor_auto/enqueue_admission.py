"""Enqueue-time work_key derivation and execution-mode receipt (parallel child lanes).

When a distinct-thread child omits ``work_key``, derive ``agent-bus:<thread_id>`` so
lane-B conductor concurrent admission can engage without silent serial degrade.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from work_key_grammar import normalize_work_key

from services.git_integration_worker.cursor_auto.execution_mode import (
    DEFAULT_EXECUTION_MODE,
    LANE_CONDUCTOR_CONTRACTS,
    ExecutionModeResolution,
    resolve_execution_mode_at_enqueue,
)

WorkKeySource = Literal["wire", "derived"]
SerialReason = Literal[
    "same_thread",
    "lane_a",
    "same_work_key_active",
    "explicit_serial",
    "contract_not_concurrent",
]

_PROPAGATE = "propagate"


@dataclass(frozen=True, slots=True)
class WorkKeyResolution:
    work_key: str | None
    source: WorkKeySource | None


@dataclass(frozen=True, slots=True)
class EnqueueAdmissionResolution:
    work_key: str | None
    work_key_source: WorkKeySource | None
    execution_mode: str
    execution_mode_declare_reason: str
    serial_reason: SerialReason | None
    effective_lane: str | None


def is_distinct_thread_child(
    *,
    lane_role: str | None,
    parent_thread: str | None,
    lane: str | None,
) -> bool:
    """True when the request is a child lane on its own thread (parallel-by-default)."""
    if str(lane or "").strip().upper() == "B":
        return True
    role = str(lane_role or "").strip()
    parent = str(parent_thread or "").strip()
    if role == "sub_mission" and parent and parent.lower() != "none":
        return True
    return False


def derive_thread_work_key(thread_id: str) -> str:
    """Normalize child thread id into the D4 ``agent-bus:`` work identity."""
    slug = str(thread_id).strip()
    return f"agent-bus:{slug}"


def resolve_work_key_at_enqueue(
    *,
    thread_id: str,
    wire_work_key: str | None,
    lane_role: str | None = None,
    parent_thread: str | None = None,
    lane: str | None = None,
) -> WorkKeyResolution:
    normalized = normalize_work_key(wire_work_key)
    if normalized:
        return WorkKeyResolution(work_key=normalized, source="wire")
    if is_distinct_thread_child(
        lane_role=lane_role, parent_thread=parent_thread, lane=lane
    ):
        return WorkKeyResolution(
            work_key=derive_thread_work_key(thread_id),
            source="derived",
        )
    return WorkKeyResolution(work_key=None, source=None)


def _same_thread_inflight(queue: Any, thread_id: str) -> bool:
    counts = queue.in_memory_thread_lane_counts(thread_id, exclude_job_id=None)
    return bool(counts.get("same_thread_pending") or counts.get("same_thread_claimed"))


def _same_work_key_claimed(queue: Any, work_key: str | None) -> bool:
    return bool(queue.has_claimed_concurrent_work_key(work_key))


def _serial_resolution(
    *,
    wk: WorkKeyResolution,
    effective_lane: str | None,
    reason: SerialReason,
) -> EnqueueAdmissionResolution:
    return EnqueueAdmissionResolution(
        work_key=wk.work_key,
        work_key_source=wk.source,
        execution_mode=DEFAULT_EXECUTION_MODE,
        execution_mode_declare_reason="default",
        serial_reason=reason,
        effective_lane=effective_lane,
    )


def resolve_enqueue_admission(
    *,
    queue: Any,
    thread_id: str,
    contract: str,
    requested_execution_mode: str | None,
    execution_mode_wire_set: bool,
    continuity_hop: bool,
    lane: str | None,
    wire_work_key: str | None,
    lane_role: str | None = None,
    parent_thread: str | None = None,
) -> EnqueueAdmissionResolution:
    """Single enqueue authority: work_key (wire|derived) + mode + serial_reason."""
    wk = resolve_work_key_at_enqueue(
        thread_id=thread_id,
        wire_work_key=wire_work_key,
        lane_role=lane_role,
        parent_thread=parent_thread,
        lane=lane,
    )
    effective_lane = lane
    if wk.source == "derived" and not str(effective_lane or "").strip():
        effective_lane = "B"

    requested = (requested_execution_mode or DEFAULT_EXECUTION_MODE).strip() or (
        DEFAULT_EXECUTION_MODE
    )
    contract_norm = str(contract or "").strip().lower()

    if continuity_hop:
        mode = resolve_execution_mode_at_enqueue(
            contract=contract,
            requested=requested,
            continuity_hop=True,
            lane=effective_lane,
            work_key=wk.work_key,
        )
        serial_reason: SerialReason | None = (
            "explicit_serial" if mode.mode == DEFAULT_EXECUTION_MODE else None
        )
        return EnqueueAdmissionResolution(
            work_key=wk.work_key,
            work_key_source=wk.source,
            execution_mode=mode.mode,
            execution_mode_declare_reason=mode.reason,
            serial_reason=serial_reason,
            effective_lane=effective_lane,
        )

    if _same_thread_inflight(queue, thread_id):
        return _serial_resolution(
            wk=wk, effective_lane=effective_lane, reason="same_thread"
        )
    if str(effective_lane or "").strip().upper() == "A":
        return _serial_resolution(wk=wk, effective_lane=effective_lane, reason="lane_a")
    if wk.work_key and _same_work_key_claimed(queue, wk.work_key):
        return _serial_resolution(
            wk=wk, effective_lane=effective_lane, reason="same_work_key_active"
        )
    if execution_mode_wire_set and requested == DEFAULT_EXECUTION_MODE:
        return _serial_resolution(
            wk=wk, effective_lane=effective_lane, reason="explicit_serial"
        )
    if contract_norm not in LANE_CONDUCTOR_CONTRACTS and contract_norm != _PROPAGATE:
        return _serial_resolution(
            wk=wk,
            effective_lane=effective_lane,
            reason="contract_not_concurrent",
        )

    mode: ExecutionModeResolution = resolve_execution_mode_at_enqueue(
        contract=contract,
        requested=requested,
        continuity_hop=False,
        lane=effective_lane,
        work_key=wk.work_key,
    )
    serial_reason = None
    if mode.mode == DEFAULT_EXECUTION_MODE:
        if mode.reason == "predicate_unmet_requested_declined":
            serial_reason = "explicit_serial"
        else:
            serial_reason = "contract_not_concurrent"

    return EnqueueAdmissionResolution(
        work_key=wk.work_key,
        work_key_source=wk.source,
        execution_mode=mode.mode,
        execution_mode_declare_reason=mode.reason,
        serial_reason=serial_reason,
        effective_lane=effective_lane,
    )


def format_work_key_receipt(
    work_key: str | None, source: WorkKeySource | None
) -> str | None:
    if not work_key:
        return None
    if source:
        return f"{work_key} (source={source})"
    return work_key


def admission_execution_fields(
    resolution: EnqueueAdmissionResolution,
) -> dict[str, Any]:
    """Wire fields for ``job_admission`` and admit-turn bodies."""
    out: dict[str, Any] = {
        "execution_mode": resolution.execution_mode,
    }
    wk_line = format_work_key_receipt(resolution.work_key, resolution.work_key_source)
    if wk_line:
        out["work_key"] = wk_line
    if resolution.work_key_source:
        out["work_key_source"] = resolution.work_key_source
    if resolution.execution_mode == DEFAULT_EXECUTION_MODE and resolution.serial_reason:
        out["serial_reason"] = resolution.serial_reason
    return out
