"""Event signals for SDK-substrate generate dispatch."""

from __future__ import annotations

from typing import Any, Literal

from .events import (
    FrontierHandoffCreated,
    FrontierSdkCostRiskWarning,
    FrontierSdkGenerateRequested,
    FrontierSdkKnobDropped,
    FrontierSdkMaterializationIncomplete,
    FrontierSdkReasoningEffortRejected,
    FrontierSdkWorkerDispatched,
    FrontierSdkWorkerDispatchFailed,
    FrontierSdkWorkerQueued,
)
from .story_wire import build_association_envelope


def publish_frontier_event(event: Any) -> None:
    try:
        from systems.proxy.dependencies import get_proxy

        proxy = get_proxy()
        event_bus = getattr(proxy, "event_bus", None)
        if event_bus is not None:
            event_bus.publish_from_sync(event)
    except Exception:
        return


def emit_sdk_generate_requested(
    *,
    request_id: str,
    role: str,
    execution_id: str,
    handoff_contract: str,
    resolved_model: str,
) -> None:
    publish_frontier_event(
        FrontierSdkGenerateRequested(
            request_id=request_id,
            role=role,
            execution_id=execution_id,
            handoff_contract=handoff_contract,
            resolved_model=resolved_model,
        )
    )


def emit_sdk_thread_created(
    *, request_id: str, to_agent: str, thread_id: str, reused: bool = False
) -> None:
    publish_frontier_event(
        FrontierHandoffCreated(
            request_id=request_id,
            to_agent=to_agent,
            thread_id=thread_id,
            reused=reused,
        )
    )


def emit_sdk_worker_outcome(
    *,
    request_id: str,
    thread_id: str,
    execution_id: str,
    worker_ok: bool,
    worker_detail: dict[str, Any],
    caller_agent: str | None = None,
    purpose_body: str | None = None,
    packet_path: str | None = None,
) -> None:
    envelope = build_association_envelope(
        purpose_body=purpose_body,
        caller_agent=caller_agent,
        request_id=request_id,
        dispatch_id=(
            str(worker_detail["dispatch_id"])
            if worker_detail.get("dispatch_id")
            else None
        ),
        packet_path=packet_path,
    )
    association = {
        "asked_by": envelope.asked_by,
        "purpose": envelope.purpose,
        "story_id": envelope.story_id,
    }
    if worker_ok:
        if worker_detail.get("queued"):
            ticket = worker_detail.get("ticket") or {}
            queued_dispatch_id = ticket.get("dispatch_id") or worker_detail.get(
                "dispatch_id"
            )
            publish_frontier_event(
                FrontierSdkWorkerQueued(
                    request_id=request_id,
                    thread_id=thread_id,
                    execution_id=execution_id,
                    dispatch_id=queued_dispatch_id or None,
                    queue_position=ticket.get("queue_position"),
                    admitted_via="stargate",
                    seat="cursor-sdk",
                    **association,
                )
            )
            return
        publish_frontier_event(
            FrontierSdkWorkerDispatched(
                request_id=request_id,
                thread_id=thread_id,
                execution_id=execution_id,
                dispatch_id=worker_detail.get("dispatch_id"),
                admitted_via="stargate",
                seat="cursor-sdk",
                **association,
            )
        )
        return
    publish_frontier_event(
        FrontierSdkWorkerDispatchFailed(
            request_id=request_id,
            thread_id=thread_id,
            execution_id=execution_id,
            error="worker_dispatch: failed",
            status_code=worker_detail.get("status_code"),
            code=worker_detail.get("code"),
            blocking_dispatch_id=worker_detail.get("blocking_dispatch_id"),
            failure_layer=worker_detail.get("failure_layer"),
            transport_error_kind=worker_detail.get("transport_error_kind"),
            dispatch_id=worker_detail.get("dispatch_id"),
            detail_summary=worker_detail.get("detail_summary")
            or worker_detail.get("message"),
            http_status=worker_detail.get("http_status"),
            worker_error_code=worker_detail.get("worker_error_code"),
        )
    )


def emit_sdk_materialization_incomplete(
    *,
    request_id: str,
    packet_path: str,
    probe_root: str,
    source_ref: str,
    execution_id: str | None = None,
    thread_id: str | None = None,
) -> None:
    publish_frontier_event(
        FrontierSdkMaterializationIncomplete(
            request_id=request_id,
            packet_path=packet_path,
            probe_root=probe_root,
            source_ref=source_ref,
            execution_id=execution_id,
            thread_id=thread_id,
            route="/frontier/dispatch",
        )
    )


def emit_sdk_cost_risk_warning(
    *,
    request_id: str | None,
    execution_id: str | None,
    model_id: str,
    contract: str,
    suppressed: bool,
    suppression_reason: str | None = None,
    cost_intent_reason: str | None = None,
    suggested_knobs: dict[str, str] | None = None,
    suggested_model: str | None = None,
) -> None:
    publish_frontier_event(
        FrontierSdkCostRiskWarning(
            request_id=request_id,
            execution_id=execution_id,
            model_id=model_id,
            contract=contract,
            suppressed=suppressed,
            suppression_reason=suppression_reason,
            cost_intent_reason=cost_intent_reason,
            suggested_knobs=suggested_knobs,
            suggested_model=suggested_model,
        )
    )


def emit_sdk_knob_dropped(
    *,
    model_id: str,
    knob: str,
    requested: str,
    reason: Literal["unsupported", "invalid_value"],
) -> None:
    publish_frontier_event(
        FrontierSdkKnobDropped(
            model_id=model_id,
            knob=knob,
            requested=requested,
            reason=reason,
        )
    )


def emit_sdk_reasoning_effort_rejected(
    *,
    model_id: str,
    requested: str,
) -> None:
    publish_frontier_event(
        FrontierSdkReasoningEffortRejected(
            model_id=model_id,
            requested=requested,
        )
    )
