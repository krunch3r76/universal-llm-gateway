"""Event factories for git-integration-worker lifecycle and dispatch reject signals."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, Literal

from universal_event_bus import Event, event_factory
from universal_logging import get_logger

logger = get_logger(__name__)

FailureLayer = Literal[
    "transport",
    "http",
    "validation",
    "materialization",
    "admission",
    "worker_runtime",
]
TransportErrorKind = Literal[
    "connect_refused",
    "timeout",
    "dns",
    "tls",
    "bad_response",
    "unknown",
]

_uds_publisher: Callable[[str, dict[str, Any]], None] | None = None


def register_git_worker_lifecycle_event_publisher(
    publisher: Callable[[str, dict[str, Any]], None],
) -> None:
    global _uds_publisher
    _uds_publisher = publisher


try:
    from mcp_events import record
except ImportError:

    def record(signal: str, **payload: Any) -> None:  # type: ignore[misc]
        if _uds_publisher is None:
            return
        _uds_publisher(signal, dict(payload))


def _emit(event: Event) -> None:
    record(event.signal, **event.payload)


def request_id_from_dispatch_id(dispatch_id: str) -> str:
    """Derive envelope ``request_id`` from ``{request_id}-{8hex}`` dispatch ids.

    Dual-field contract (sdk019): this dispatch-derived value is the lifecycle
    envelope ``request_id`` and is never overwritten by SDK stream/error ids.
    SDK run correlation uses separate ``sdk_request_id`` +
    ``request_id_source`` on ``frontier.sdk.worker.completed``.
    """
    if len(dispatch_id) > 9 and dispatch_id[-9] == "-":
        suffix = dispatch_id[-8:]
        if len(suffix) == 8 and all(c in "0123456789abcdef" for c in suffix):
            return dispatch_id[:-9]
    return dispatch_id


def build_dispatch_error_envelope(
    *,
    origin_service: str = "git_worker",
    schema_version: str = "1",
    request_id: str | None = None,
    correlation_id: str | None = None,
    execution_id: str | None = None,
    thread_id: str | None = None,
    dispatch_id: str | None = None,
    failure_layer: FailureLayer,
    http_status: int | None = None,
    worker_error_code: str | None = None,
    transport_error_kind: TransportErrorKind | None = None,
    route: str | None = None,
    method: str | None = None,
    detail_summary: str | None = None,
    invalid_fields: list[str] | None = None,
    retryable: bool | None = None,
    validation_stage: str | None = None,
) -> dict[str, Any]:
    """Shared spine envelope for worker dispatch failure events and G5 logs."""
    resolved_request_id = request_id
    if resolved_request_id is None and dispatch_id is not None:
        resolved_request_id = request_id_from_dispatch_id(dispatch_id)
    envelope: dict[str, Any] = {
        "origin_service": origin_service,
        "schema_version": schema_version,
        "failure_layer": failure_layer,
    }
    if resolved_request_id is not None:
        envelope["request_id"] = resolved_request_id
        envelope["correlation_id"] = correlation_id or resolved_request_id
    if execution_id is not None:
        envelope["execution_id"] = execution_id
    if thread_id is not None:
        envelope["thread_id"] = thread_id
    if dispatch_id is not None:
        envelope["dispatch_id"] = dispatch_id
    if http_status is not None:
        envelope["http_status"] = http_status
    if worker_error_code is not None:
        envelope["worker_error_code"] = worker_error_code
    if transport_error_kind is not None:
        envelope["transport_error_kind"] = transport_error_kind
    if route is not None:
        envelope["route"] = route
    if method is not None:
        envelope["method"] = method
    if detail_summary is not None:
        envelope["detail_summary"] = detail_summary
    if invalid_fields:
        envelope["invalid_fields"] = invalid_fields
    if retryable is not None:
        envelope["retryable"] = retryable
    if validation_stage is not None:
        envelope["validation_stage"] = validation_stage
    return envelope


@event_factory
def GitWorkerStarted(  # noqa: N802
    worker_id: str,
    pid: int,
    port: int,
    version: str,
    origin_service: str,
    schema_version: str,
    started_at: str,
    source_repo: str,
    bind_host: str,
    build_sha: str | None = None,
    health_url: str | None = None,
) -> Event:
    payload: dict[str, Any] = {
        "worker_id": worker_id,
        "pid": pid,
        "port": port,
        "version": version,
        "origin_service": origin_service,
        "schema_version": schema_version,
        "started_at": started_at,
        "source_repo": source_repo,
        "bind_host": bind_host,
    }
    if build_sha is not None:
        payload["build_sha"] = build_sha
    if health_url is not None:
        payload["health_url"] = health_url
    return Event(signal="git_worker.started", payload=payload, scope="node")


@event_factory
def GitWorkerDispatchRejected(  # noqa: N802
    envelope: dict[str, Any],
) -> Event:
    return Event(signal="git_worker.dispatch.rejected", payload=envelope, scope="node")


def emit_git_worker_started(
    *,
    worker_id: str,
    pid: int,
    port: int,
    version: str,
    started_at: str,
    source_repo: str,
    bind_host: str,
    build_sha: str | None = None,
    health_url: str | None = None,
) -> None:
    _emit(
        GitWorkerStarted(
            worker_id=worker_id,
            pid=pid,
            port=port,
            version=version,
            origin_service="git_worker",
            schema_version="1",
            started_at=started_at,
            source_repo=source_repo,
            bind_host=bind_host,
            build_sha=build_sha,
            health_url=health_url,
        )
    )


def emit_git_worker_dispatch_rejected(envelope: dict[str, Any]) -> None:
    _emit(GitWorkerDispatchRejected(envelope=envelope))


def log_dispatch_rejection(envelope: dict[str, Any]) -> None:
    logger.warning("git_worker.dispatch.rejected: %s", json.dumps(envelope, sort_keys=True))
