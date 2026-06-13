"""Event signals for SDK-substrate generate dispatch."""

from __future__ import annotations

from typing import Any

from .events import (
    FrontierHandoffCreated,
    FrontierSdkGenerateRequested,
    FrontierSdkWorkerDispatched,
    FrontierSdkWorkerDispatchFailed,
)


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
    worker_warning: str | None,
) -> None:
    if worker_ok:
        publish_frontier_event(
            FrontierSdkWorkerDispatched(
                request_id=request_id,
                thread_id=thread_id,
                execution_id=execution_id,
            )
        )
        return
    publish_frontier_event(
        FrontierSdkWorkerDispatchFailed(
            request_id=request_id,
            thread_id=thread_id,
            execution_id=execution_id,
            error=worker_warning or "worker_dispatch: failed",
        )
    )
