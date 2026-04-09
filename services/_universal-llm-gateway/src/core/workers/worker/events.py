"""Worker event emission to local Event Service.

Fire-and-forget NDJSON to the ingest socket. Never blocks the inference path.
Silent on failure (event service may not be running in dev).
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)

_EVENTS_SOCK = os.environ.get(
    "EVENTS_INGEST_SOCK",
    "/tmp/universal-protocol/events.sock",
)

_publisher: Any = None


async def _get_publisher() -> Any:
    global _publisher  # noqa: PLW0603
    if _publisher is None:
        from universal_event_bus.events.debug_broadcaster import UDSEventPublisher

        _publisher = UDSEventPublisher(_EVENTS_SOCK, maxsize=200)
        await _publisher.start()
    return _publisher


def _make_event(
    signal: str,
    payload: dict[str, Any],
    *,
    scope: str = "node",
    role: str = "observation",
) -> dict[str, Any]:
    ts_ms = int(time.time() * 1000)
    return {
        "signal": signal,
        "role": role,
        "scope": scope,
        "ts_unix_ms": ts_ms,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": "worker",
        "payload": payload,
    }


async def emit_worker_started(*, worker_id: str, model_id: str, pid: int) -> None:
    pub = await _get_publisher()
    pub.publish_nowait(
        _make_event(
            "worker.started",
            {"worker_id": worker_id, "model_id": model_id, "pid": pid},
        )
    )


async def emit_worker_stopped(
    *, worker_id: str, model_id: str, exit_code: int | None
) -> None:
    pub = await _get_publisher()
    pub.publish_nowait(
        _make_event(
            "worker.stopped",
            {"worker_id": worker_id, "model_id": model_id, "exit_code": exit_code},
            scope="global",
        )
    )


async def emit_model_loading(*, worker_id: str, model_id: str) -> None:
    pub = await _get_publisher()
    pub.publish_nowait(
        _make_event(
            "worker.model.loading",
            {"worker_id": worker_id, "model_id": model_id},
        )
    )


async def emit_model_loaded(
    *, worker_id: str, model_id: str, duration_s: float
) -> None:
    pub = await _get_publisher()
    pub.publish_nowait(
        _make_event(
            "worker.model.loaded",
            {
                "worker_id": worker_id,
                "model_id": model_id,
                "duration_s": round(duration_s, 3),
            },
            scope="global",
        )
    )


async def emit_model_failed(*, worker_id: str, model_id: str, error: str) -> None:
    pub = await _get_publisher()
    pub.publish_nowait(
        _make_event(
            "worker.model.failed",
            {"worker_id": worker_id, "model_id": model_id, "error": error},
            scope="global",
        )
    )


async def emit_inference_dequeued(
    *,
    worker_id: str,
    model_id: str,
    request_id: str,
    queue_wait_ms: float,
) -> None:
    """Emit when inference slot is acquired from FifoCapacityGate.

    Coordination-role event: marks the boundary between queue-wait and
    active inference. queue_wait_ms is the time spent waiting for the slot,
    allowing precise latency decomposition (queue vs. inference).
    """
    pub = await _get_publisher()
    pub.publish_nowait(
        _make_event(
            "inference.dequeued",
            {
                "worker_id": worker_id,
                "model_id": model_id,
                "request_id": request_id,
                "queue_wait_ms": round(queue_wait_ms, 1),
            },
            role="coordination",
        )
    )


async def emit_inference_started(
    *, worker_id: str, model_id: str, request_id: str
) -> None:
    pub = await _get_publisher()
    pub.publish_nowait(
        _make_event(
            "worker.inference.started",
            {
                "worker_id": worker_id,
                "model_id": model_id,
                "request_id": request_id,
            },
        )
    )


async def emit_inference_completed(
    *,
    worker_id: str,
    model_id: str,
    request_id: str,
    duration_s: float,
    tokens: int | None = None,
) -> None:
    payload: dict[str, Any] = {
        "worker_id": worker_id,
        "model_id": model_id,
        "request_id": request_id,
        "duration_s": round(duration_s, 3),
    }
    if tokens is not None:
        payload["tokens"] = tokens
    pub = await _get_publisher()
    pub.publish_nowait(_make_event("worker.inference.completed", payload))


async def shutdown_publisher() -> None:
    global _publisher  # noqa: PLW0603
    if _publisher is not None:
        await _publisher.stop()
        _publisher = None
