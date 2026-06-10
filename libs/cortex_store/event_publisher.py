"""Standalone UDS event publisher for cortex-api.

Follows the same NDJSON wire format as mcp_events.py so the event service
receives cortex-api lifecycle signals (e.g. mcp.session.close.atomic,
mcp.session.close.rejected) without requiring the mcp-server
``request_profile`` dependency.

\u2200 emit call: fire-and-forget; drops oldest if queue is full, never blocks
the caller. Falls back silently if the event service socket is unavailable.

\u2200 signal: stdlib + universal_logging only \u2014 no mcp-server imports allowed.
"""

from __future__ import annotations

import json
import os
import queue
import socket
import threading
import time
from datetime import UTC, datetime
from typing import Any

from universal_event_bus.events import Event
from universal_event_bus.events.factory import event_factory
from universal_logging import get_logger

logger = get_logger(__name__)

_EVENTS_SOCK = os.getenv("EVENTS_INGEST_SOCK", "/tmp/universal-protocol/events.sock")
_ENABLED = os.getenv("CORTEX_EVENTS_ENABLED", "true").lower() in ("true", "1", "yes")
_QUEUE_MAX = 500
_RECONNECT_DELAY = 5.0
_SEND_TIMEOUT = 2.0


class _UDSPublisher:
    """Thread-based UDS publisher with bounded queue and auto-reconnect.

    \u2200 event: either delivered or dropped (never blocks the caller).
    Drop policy: drop-oldest when queue full.
    """

    def __init__(self, sock_path: str) -> None:
        self._sock_path = sock_path
        self._q: queue.Queue[str] = queue.Queue(maxsize=_QUEUE_MAX)
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="cortex-api-events-uds"
        )
        self._thread.start()

    def put_nowait(self, line: str) -> None:
        try:
            self._q.put_nowait(line)
        except queue.Full:
            try:
                self._q.get_nowait()
            except queue.Empty:
                pass
            try:
                self._q.put_nowait(line)
            except queue.Full:
                logger.warning(
                    "cortex-api event publisher queue full; event dropped",
                )

    def _run(self) -> None:
        sock: socket.socket | None = None
        while True:
            if sock is None:
                try:
                    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    sock.settimeout(_SEND_TIMEOUT)
                    sock.connect(self._sock_path)
                except OSError as e:
                    logger.warning("cortex-api event publisher connect error: %s", e)
                    try:
                        sock.close()
                    except OSError:
                        pass
                    sock = None
                    time.sleep(_RECONNECT_DELAY)
                    continue
            try:
                line = self._q.get(timeout=1.0)
                sock.sendall(line.encode())
            except queue.Empty:
                continue
            except OSError as send_error:
                logger.warning("cortex-api event publisher send failed: %s", send_error)
                try:
                    sock.close()
                except OSError as close_error:
                    logger.warning(
                        "cortex-api event publisher socket close failed: %s",
                        close_error,
                    )
                sock = None
                time.sleep(_RECONNECT_DELAY)


_publisher: _UDSPublisher | None = _UDSPublisher(_EVENTS_SOCK) if _ENABLED else None


def record(signal: str, **payload: Any) -> None:
    """Publish a structured event to the event service via UDS.

    Wire format mirrors mcp_events.py \u2014 source is ``cortex-api`` here since
    cortex_store runs inside the cortex-api process, not the mcp-server.
    """
    if _publisher is None:
        return
    now = datetime.now(UTC)
    event: dict[str, Any] = {
        "signal": signal,
        "source": "cortex-api",
        "role": "observation",
        "scope": "global",
        "timestamp": now.isoformat(),
        "ts_unix_ms": int(now.timestamp() * 1000),
        "payload": payload,
    }
    _publisher.put_nowait(json.dumps(event, default=str) + "\n")


@event_factory
def cortex_subgraph_render_called(
    render_id: str,
    root: str,
    hops: int,
    edge_types_count: int,
    top_k_assertions: int,
    include_superseded: bool,
) -> Event:
    """cortex.subgraph.render.called \u2014 emitted at entry to render_subgraph (V1.1)."""
    ev = Event(
        signal="cortex.subgraph.render.called",
        role="observation",
        scope="global",
        payload={
            "render_id": render_id,
            "root": root,
            "hops": hops,
            "edge_types_count": edge_types_count,
            "top_k_assertions": top_k_assertions,
            "include_superseded": include_superseded,
        },
    )
    record(ev.signal, **ev.payload)
    return ev


@event_factory
def cortex_subgraph_render_completed(
    render_id: str,
    root: str,
    hops: int,
    entity_count: int,
    edge_count: int,
    duration_ms: int,
    rendered_bytes: int,
) -> Event:
    """cortex.subgraph.render.completed \u2014 emitted on successful render (V1.1)."""
    ev = Event(
        signal="cortex.subgraph.render.completed",
        role="observation",
        scope="global",
        payload={
            "render_id": render_id,
            "root": root,
            "hops": hops,
            "entity_count": entity_count,
            "edge_count": edge_count,
            "duration_ms": duration_ms,
            "rendered_bytes": rendered_bytes,
        },
    )
    record(ev.signal, **ev.payload)
    return ev


@event_factory
def cortex_subgraph_render_failed(
    render_id: str,
    root: str,
    reason: str,
    hops: int,
) -> Event:
    """cortex.subgraph.render.failed \u2014 emitted on error paths inside render_subgraph (V1.1).

    The ``reason`` enum widens beyond V1.1 spec to carry field-level granularity:
    ``root_missing``, ``hops_out_of_range``, ``top_k_out_of_range``,
    ``unknown_edge_type``, ``entity_not_found``, ``entity_cap_exceeded``,
    ``card_build_failed``. The grok V1 stub collapsed every validation
    failure to ``"validation_error"`` \u2014 fixed in this revision.
    """
    ev = Event(
        signal="cortex.subgraph.render.failed",
        role="observation",
        scope="global",
        payload={
            "render_id": render_id,
            "root": root,
            "reason": reason,
            "hops": hops,
        },
    )
    record(ev.signal, **ev.payload)
    return ev


@event_factory
def cortex_search_failed(
    exc_type: str,
    detail: str,
    q_len: int,
    intent: str,
) -> Event:
    """cortex.search.failed — emitted at search boundary before re-raise."""
    ev = Event(
        signal="cortex.search.failed",
        role="observation",
        scope="global",
        payload={
            "exc_type": exc_type,
            "detail": detail,
            "q_len": q_len,
            "intent": intent,
        },
    )
    record(ev.signal, **ev.payload)
    return ev


@event_factory
def cortex_search_vector_degraded(
    reason: str,
    exc_type: str,
    q_len: int,
    duration_s: float,
) -> Event:
    """cortex.search.vector.degraded — vector branch failed; FTS-only degrade."""
    ev = Event(
        signal="cortex.search.vector.degraded",
        role="observation",
        scope="global",
        payload={
            "reason": reason,
            "exc_type": exc_type,
            "q_len": q_len,
            "duration_s": duration_s,
        },
    )
    record(ev.signal, **ev.payload)
    return ev


@event_factory
def cortex_entity_source_changed(
    entity_id: str,
    change: str,
    source_uri: str | None = None,
) -> Event:
    """cortex.entity.source.changed — emitted when an entity's source_uri is
    set, changed, or dropped. Drives the RAG EntityAdmissionGate dirty-flag +
    debounced full re-fetch (plan:rag-entity-gated-indexing Phase 2).

    change ∈ {"set", "changed", "dropped"}. Fire-and-forget refresh nudge — a
    periodic backstop in the gate self-heals a missed emission, so correctness
    never depends on this event firing (thread 1136 A6).
    """
    ev = Event(
        signal="cortex.entity.source.changed",
        role="observation",
        scope="global",
        payload={
            "entity_id": entity_id,
            "change": change,
            **({"source_uri": source_uri} if source_uri is not None else {}),
        },
    )
    record(ev.signal, **ev.payload)
    return ev
