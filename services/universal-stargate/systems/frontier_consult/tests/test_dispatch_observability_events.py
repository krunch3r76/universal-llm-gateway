"""Unit tests for SDK dispatch observability event factories (Stargate)."""

from __future__ import annotations

from systems.frontier_consult.events import (
    FrontierSdkMaterializationIncomplete,
    FrontierSdkWorkerDispatchFailed,
)


def test_frontier_sdk_worker_dispatch_failed_transport_envelope() -> None:
    event = FrontierSdkWorkerDispatchFailed(
        request_id="req-1",
        thread_id="thread-1",
        execution_id="exec-1",
        error="worker_dispatch: failed",
        status_code=599,
        code="CURSOR_WORKER_UNREACHABLE",
        failure_layer="transport",
        transport_error_kind="connect_refused",
        dispatch_id="req-1-aabbccdd",
        detail_summary="Connection refused",
        worker_error_code="CURSOR_WORKER_UNREACHABLE",
    )
    assert event.signal == "frontier.sdk.worker.failed"
    assert event.payload["error"] == "worker_dispatch: failed"
    assert event.payload["origin_service"] == "stargate"
    assert event.payload["failure_layer"] == "transport"
    assert event.payload["transport_error_kind"] == "connect_refused"
    assert event.payload["http_status"] == 599
    assert event.payload["status_code"] == 599


def test_frontier_sdk_materialization_incomplete() -> None:
    event = FrontierSdkMaterializationIncomplete(
        request_id="req-2",
        packet_path="workspaces/missing/packet.md",
        probe_root="/mnt/torus/projects",
        source_ref="todo:example",
        execution_id="exec-2",
        thread_id="thread-2",
        route="/frontier/dispatch",
    )
    assert event.signal == "frontier.sdk.materialization.incomplete"
    assert event.payload["origin_service"] == "stargate"
    assert event.payload["failure_layer"] == "materialization"
    assert event.payload["packet_path"] == "workspaces/missing/packet.md"
