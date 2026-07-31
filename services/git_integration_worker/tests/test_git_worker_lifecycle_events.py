"""Unit tests for git-worker lifecycle and dispatch reject event factories."""

from __future__ import annotations

from services.git_integration_worker.cursor_sdk_events import FrontierSdkWorkerFailed
from services.git_integration_worker.git_worker_lifecycle_events import (
    GitWorkerDispatchRejected,
    GitWorkerStarted,
    build_dispatch_error_envelope,
    request_id_from_dispatch_id,
)


def test_request_id_from_dispatch_id() -> None:
    assert request_id_from_dispatch_id("req-abc-12345678") == "req-abc"
    assert request_id_from_dispatch_id("plain-id") == "plain-id"


def test_git_worker_started_payload() -> None:
    event = GitWorkerStarted(
        worker_id="wid-1",
        pid=4242,
        port=8091,
        version="abc1234",
        origin_service="git_worker",
        schema_version="1",
        started_at="2026-06-22T00:00:00+00:00",
        source_repo="/mnt/torus/projects/universal-llm-gateway",
        bind_host="127.0.0.1",
        build_sha="abc1234",
    )
    assert event.signal == "git_worker.started"
    assert event.payload["worker_id"] == "wid-1"
    assert event.payload["origin_service"] == "git_worker"
    assert event.payload["port"] == 8091


def test_git_worker_dispatch_rejected_envelope() -> None:
    envelope = build_dispatch_error_envelope(
        dispatch_id="req-1-aabbccdd",
        execution_id="exec-1",
        thread_id="thread-1",
        failure_layer="validation",
        http_status=422,
        worker_error_code="CURSOR_PACKET_INVALID",
        route="/api/v1/cursor/dispatch",
        method="POST",
        detail_summary="packet_path not found: missing.md",
        invalid_fields=["packet_path"],
    )
    event = GitWorkerDispatchRejected(envelope=envelope)
    assert event.signal == "git_worker.dispatch.rejected"
    assert event.payload["request_id"] == "req-1"
    assert event.payload["worker_error_code"] == "CURSOR_PACKET_INVALID"
    assert event.payload["failure_layer"] == "validation"
    assert "packet_path" in event.payload["invalid_fields"]


def test_frontier_sdk_worker_failed_worker_origin() -> None:
    event = FrontierSdkWorkerFailed(
        dispatch_id="disp-1",
        thread_id="thread-1",
        execution_id="exec-1",
        error="RuntimeError: bridge died",
        failure_layer="worker_runtime",
        worker_error_code="CURSOR_SDK_DISPATCH",
        detail_summary="RuntimeError: bridge died",
    )
    assert event.signal == "frontier.sdk.worker.failed"
    assert event.payload["origin_service"] == "git_worker"
    assert event.payload["failure_layer"] == "worker_runtime"
