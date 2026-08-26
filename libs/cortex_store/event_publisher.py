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
def cortex_subgraph_walk_called(
    walk_id: str,
    root: str,
    hops: int,
    edge_types_count: int,
    direction: str,
    entity_cap: int,
    include_counts: bool,
    promote_hubs: bool,
) -> Event:
    """cortex.subgraph.walk.called — emitted at entry to walk_subgraph."""
    ev = Event(
        signal="cortex.subgraph.walk.called",
        role="observation",
        scope="global",
        payload={
            "walk_id": walk_id,
            "root": root,
            "hops": hops,
            "edge_types_count": edge_types_count,
            "direction": direction,
            "entity_cap": entity_cap,
            "include_counts": include_counts,
            "promote_hubs": promote_hubs,
        },
    )
    record(ev.signal, **ev.payload)
    return ev


@event_factory
def cortex_subgraph_walk_completed(
    walk_id: str,
    root: str,
    hops: int,
    entity_count: int,
    edge_count: int,
    duration_ms: int,
    envelope_bytes: int,
    table_bytes: int,
) -> Event:
    """cortex.subgraph.walk.completed — emitted on successful walk."""
    ev = Event(
        signal="cortex.subgraph.walk.completed",
        role="observation",
        scope="global",
        payload={
            "walk_id": walk_id,
            "root": root,
            "hops": hops,
            "entity_count": entity_count,
            "edge_count": edge_count,
            "duration_ms": duration_ms,
            "envelope_bytes": envelope_bytes,
            "table_bytes": table_bytes,
        },
    )
    record(ev.signal, **ev.payload)
    return ev


@event_factory
def cortex_subgraph_walk_failed(
    walk_id: str,
    root: str,
    reason: str,
    hops: int,
) -> Event:
    """cortex.subgraph.walk.failed — emitted on error paths inside walk_subgraph."""
    ev = Event(
        signal="cortex.subgraph.walk.failed",
        role="observation",
        scope="global",
        payload={
            "walk_id": walk_id,
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


@event_factory
def cortex_skill_suggest_called(
    suggest_id: str,
    agent: str,
    transport: str,
    context_len: int,
    context_sha256: str,
    loaded_count: int,
    rerank_requested: bool,
) -> Event:
    """cortex.skill_suggest.called — entry telemetry (context hash+len only)."""
    ev = Event(
        signal="cortex.skill_suggest.called",
        role="observation",
        scope="global",
        payload={
            "suggest_id": suggest_id,
            "agent": agent,
            "transport": transport,
            "context_len": context_len,
            "context_sha256": context_sha256,
            "loaded_count": loaded_count,
            "rerank_requested": rerank_requested,
        },
    )
    record(ev.signal, **ev.payload)
    return ev


@event_factory
def cortex_skill_suggest_completed(
    suggest_id: str,
    agent: str,
    candidate_count: int,
    suggested_count: int,
    omitted_count: int,
    ranker_status: str,
    latency_ms: int,
    rank_execution_id: str | None = None,
) -> Event:
    """cortex.skill_suggest.completed — successful suggest path."""
    payload: dict[str, Any] = {
        "suggest_id": suggest_id,
        "agent": agent,
        "candidate_count": candidate_count,
        "suggested_count": suggested_count,
        "omitted_count": omitted_count,
        "ranker_status": ranker_status,
        "latency_ms": latency_ms,
    }
    if rank_execution_id:
        payload["rank_execution_id"] = rank_execution_id
    ev = Event(
        signal="cortex.skill_suggest.completed",
        role="observation",
        scope="global",
        payload=payload,
    )
    record(ev.signal, **ev.payload)
    return ev


@event_factory
def cortex_skill_suggest_degraded(
    suggest_id: str,
    ranker_status: str,
    degraded_reason: str,
    latency_ms: int,
) -> Event:
    """cortex.skill_suggest.degraded — rerank requested but Stage-A returned."""
    ev = Event(
        signal="cortex.skill_suggest.degraded",
        role="observation",
        scope="global",
        payload={
            "suggest_id": suggest_id,
            "ranker_status": ranker_status,
            "degraded_reason": degraded_reason,
            "latency_ms": latency_ms,
        },
    )
    record(ev.signal, **ev.payload)
    return ev


@event_factory
def cortex_skill_suggest_failed(
    suggest_id: str,
    exc_type: str,
    detail: str,
) -> Event:
    """cortex.skill_suggest.failed — true endpoint errors (not rerank degrade)."""
    ev = Event(
        signal="cortex.skill_suggest.failed",
        role="observation",
        scope="global",
        payload={
            "suggest_id": suggest_id,
            "exc_type": exc_type,
            "detail": detail,
        },
    )
    record(ev.signal, **ev.payload)
    return ev


@event_factory
def cortex_pinned_deliverable_written(
    rel_path: str,
    dispatch_id: str | None = None,
    thread_id: str | None = None,
    skipped: bool | None = None,
) -> Event:
    """cortex.pinned_deliverable.written — emitted when a packet-pinned deliverable is written to cortex (friction 19916)."""
    ev = Event(
        signal="cortex.pinned_deliverable.written",
        role="observation",
        scope="global",
        payload={
            "rel_path": rel_path,
            "dispatch_id": dispatch_id,
            "thread_id": thread_id,
            "skipped": skipped,
        },
    )
    record(ev.signal, **ev.payload)
    return ev


@event_factory
def cortex_skill_graph_drift_checked(
    *,
    drift_count: int,
    stale_edges: int,
    missing_edges: int,
    last_clean_ts: str | None,
    clean: bool,
    exit_code: int,
    consecutive_dirty_runs: int,
) -> Event:
    """cortex.skill_graph.drift.checked — periodic read-only drift metrics."""
    ev = Event(
        signal="cortex.skill_graph.drift.checked",
        role="observation",
        scope="global",
        payload={
            "drift_count": drift_count,
            "stale_edges": stale_edges,
            "missing_edges": missing_edges,
            "last_clean_ts": last_clean_ts,
            "clean": clean,
            "exit_code": exit_code,
            "consecutive_dirty_runs": consecutive_dirty_runs,
        },
    )
    record(ev.signal, **ev.payload)
    return ev


@event_factory
def cortex_skill_graph_drift_alert(
    *,
    drift_count: int,
    stale_edges: int,
    missing_edges: int,
    consecutive_dirty_runs: int,
    thread: str,
) -> Event:
    """cortex.skill_graph.drift.alert — hysteresis threshold breach."""
    ev = Event(
        signal="cortex.skill_graph.drift.alert",
        role="coordination",
        scope="global",
        payload={
            "drift_count": drift_count,
            "stale_edges": stale_edges,
            "missing_edges": missing_edges,
            "consecutive_dirty_runs": consecutive_dirty_runs,
            "thread": thread,
        },
    )
    record(ev.signal, **ev.payload)
    return ev


@event_factory
def cortex_skill_graph_drift_sweep_failed(*, error: str) -> Event:
    """cortex.skill_graph.drift.sweep.failed — monitor sweep exception."""
    ev = Event(
        signal="cortex.skill_graph.drift.sweep.failed",
        role="observation",
        scope="global",
        payload={"error": error},
    )
    record(ev.signal, **ev.payload)
    return ev


@event_factory
def cortex_supersede_would_reject(
    *,
    rule_ids: list[str],
    derivation_type: str,
    force: bool,
    valid_from_inherited: bool,
    parent_had_valid_from: bool,
    reject_field_origins: dict[str, str],
    mode: str,
    entity_id: str,
) -> Event:
    """cortex.supersede.would_reject — durable shadow/hard_422 reject telemetry.

    Emitted from _supersede.py whenever a supersede payload fails quality
    validation, on BOTH the shadow path (write still succeeds) and the
    hard_422 reject path (422 raised AFTER emit). Mirrors the preserved
    logger.info field set so the flip audit window survives cortex-api
    restarts (the prior /tmp log sink was truncated on restart).
    """
    ev = Event(
        signal="cortex.supersede.would_reject",
        role="observation",
        scope="global",
        payload={
            "rule_ids": rule_ids,
            "derivation_type": derivation_type,
            "force": force,
            "valid_from_inherited": valid_from_inherited,
            "parent_had_valid_from": parent_had_valid_from,
            "reject_field_origins": reject_field_origins,
            "mode": mode,
            "entity_id": entity_id,
        },
    )
    record(ev.signal, **ev.payload)
    return ev


@event_factory
def cortex_implement_recon_waived(
    *,
    todo_id: str,
    waived_by: str | None,
    reason_code: str | None,
    reason: str | None,
    spec_sha256: str | None,
    waived_at: str | None,
    stale: bool = False,
    stale_reason: str | None = None,
) -> Event:
    """cortex.implement.recon.waived — audited skeptic-gate recon waiver applied."""
    payload: dict[str, Any] = {
        "todo_id": todo_id,
        "waived_by": waived_by,
        "reason_code": reason_code,
        "reason": reason,
        "spec_sha256": spec_sha256,
        "waived_at": waived_at,
    }
    if stale:
        payload["stale"] = True
        if stale_reason is not None:
            payload["stale_reason"] = stale_reason
    ev = Event(
        signal="cortex.implement.recon.waived",
        role="observation",
        scope="global",
        payload=payload,
    )
    record(ev.signal, **ev.payload)
    return ev


@event_factory
def cortex_view_rendered(
    document_id: str,
    view_rev: int,
    mode: str,
    sections_repaired_count: int,
    delta_create_count: int,
    delta_update_count: int,
    delta_delete_count: int,
) -> Event:
    """cortex.view.rendered — emitted on register/refresh/full view_render."""
    ev = Event(
        signal="cortex.view.rendered",
        role="observation",
        scope="global",
        payload={
            "document_id": document_id,
            "view_rev": view_rev,
            "mode": mode,
            "sections_repaired_count": sections_repaired_count,
            "delta_create_count": delta_create_count,
            "delta_update_count": delta_update_count,
            "delta_delete_count": delta_delete_count,
        },
    )
    record(ev.signal, **ev.payload)
    return ev


@event_factory
def cortex_entity_id_minted(
    entity_id: str,
    entity_type: str,
    mint: str,
) -> Event:
    """cortex.entity.id.minted — server mint on create for referent types."""
    ev = Event(
        signal="cortex.entity.id.minted",
        role="observation",
        scope="global",
        payload={
            "entity_id": entity_id,
            "entity_type": entity_type,
            "mint": mint,
        },
    )
    record(ev.signal, **ev.payload)
    return ev


@event_factory
def cortex_entity_name_changed(
    entity_id: str,
    prior_name: str,
    name: str,
    prior_name_retained: bool,
) -> Event:
    """cortex.entity.name.changed — authority transition on display name."""
    ev = Event(
        signal="cortex.entity.name.changed",
        role="observation",
        scope="global",
        payload={
            "entity_id": entity_id,
            "prior_name": prior_name,
            "name": name,
            "prior_name_retained": prior_name_retained,
        },
    )
    record(ev.signal, **ev.payload)
    return ev


@event_factory
def cortex_entity_create_id_rejected(
    entity_type: str,
    supplied_id: str,
    caller: str,
) -> Event:
    """cortex.entity.create.id_rejected — id supplied for a minted type."""
    ev = Event(
        signal="cortex.entity.create.id_rejected",
        role="observation",
        scope="global",
        payload={
            "entity_type": entity_type,
            "supplied_id": supplied_id,
            "caller": caller,
        },
    )
    record(ev.signal, **ev.payload)
    return ev


@event_factory
def cortex_entity_alias_ambiguous(
    ref: str,
    entity_type: str,
    match_count: int,
) -> Event:
    """cortex.entity.alias.ambiguous — alias lookup matched multiple entities."""
    ev = Event(
        signal="cortex.entity.alias.ambiguous",
        role="observation",
        scope="global",
        payload={
            "ref": ref,
            "entity_type": entity_type,
            "match_count": match_count,
        },
    )
    record(ev.signal, **ev.payload)
    return ev


@event_factory
def cortex_entity_name_duplicate_rejected(
    entity_type: str,
    name: str,
    existing_entity_id: str,
) -> Event:
    """cortex.entity.name.duplicate_rejected — near-duplicate name blocked at create."""
    ev = Event(
        signal="cortex.entity.name.duplicate_rejected",
        role="observation",
        scope="global",
        payload={
            "entity_type": entity_type,
            "name": name,
            "existing_entity_id": existing_entity_id,
        },
    )
    record(ev.signal, **ev.payload)
    return ev
