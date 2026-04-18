"""Event store server - UDS ingest + FastAPI/uvicorn query server."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI

from .ingest import IngestServer
from .query import create_query_router
from .store import EventStore
from .subscribe import _DEFAULT_SUBSCRIBER_QUEUE_SIZE, create_subscribe_router

logger = logging.getLogger(__name__)

_SECONDS_PER_DAY = 86400
_DEFAULT_QUERY_SOCK_MODE = 0o660


def _event_timestamp() -> tuple[int, str]:
    """Return (unix_ms, ISO8601 Z) timestamp tuple for service events."""
    ts_ms = int(time.time() * 1000)
    ts_iso = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return ts_ms, ts_iso


def create_app(
    store: EventStore,
    subscriber_queues: set[asyncio.Queue[dict[str, Any]]],
    ingest: IngestServer,
    *,
    subscriber_queue_maxsize: int = _DEFAULT_SUBSCRIBER_QUEUE_SIZE,
) -> FastAPI:
    """Build the FastAPI query/subscribe application."""
    app = FastAPI(title="Event Store")
    query_router = create_query_router(store, ingest, subscriber_queues)
    subscribe_router = create_subscribe_router(
        store, subscriber_queues, subscriber_queue_maxsize=subscriber_queue_maxsize
    )
    app.include_router(query_router)
    app.include_router(subscribe_router)
    return app


async def _retention_loop(
    store: EventStore,
    *,
    retention_days: int = 7,
    max_sessions: int = 2,
) -> None:
    """Session-cap retention first, then age retention as safety net."""
    max_age_ms = retention_days * _SECONDS_PER_DAY * 1000

    async def _run(*, startup: bool) -> None:
        debug_deleted = await store.prune_debug_events()
        heartbeat_deleted = await store.prune_heartbeat_signals()
        session_deleted = await store.run_session_retention(max_sessions)
        age_deleted = 0 if startup else await store.run_retention(max_age_ms)
        if debug_deleted or heartbeat_deleted or session_deleted or age_deleted:
            if startup:
                logger.info(
                    "Retention (startup): debug=%d heartbeat=%d session=%d (keeping %d sessions)",
                    debug_deleted,
                    heartbeat_deleted,
                    session_deleted,
                    max_sessions,
                )
                return
            logger.info(
                "Retention: debug=%d heartbeat=%d session=%d age=%d (max_sessions=%d, max_days=%d)",
                debug_deleted,
                heartbeat_deleted,
                session_deleted,
                age_deleted,
                max_sessions,
                retention_days,
            )

    await _run(startup=True)
    while True:
        await asyncio.sleep(_SECONDS_PER_DAY)
        await _run(startup=False)


async def run_service(
    *,
    db_path: str = "/data/events.db",
    ingest_sock: str = os.environ.get(
        "EVENTS_INGEST_SOCK", "/tmp/universal-protocol/events.sock"
    ),
    query_sock: str = os.environ.get(
        "EVENTS_QUERY_SOCK", "/tmp/universal-protocol/events-query.sock"
    ),
    retention_days: int = 7,
    max_sessions: int = 2,
    persist: bool = True,
    tcp_enabled: bool = False,
    tcp_ingest_port: int = 7101,
    tcp_query_port: int = 7102,
    query_sock_mode: int = _DEFAULT_QUERY_SOCK_MODE,
    bridge_upstream_sock: str | None = None,
    bridge_origin_node: str | None = None,
    db_queue_maxsize: int = 10000,
    subscriber_queue_maxsize: int = _DEFAULT_SUBSCRIBER_QUEUE_SIZE,
    drop_notice_interval_sec: float = 1.0,
) -> None:
    """Main service lifecycle - parameterized for library use.

    Args:
        persist: When False, uses SQLite :memory: (no disk writes, no retention).
                 Full query/subscribe/fanout surface remains available.
        bridge_upstream_sock: When set, forward scope=global events to this
                             upstream Event Service ingest socket.
        bridge_origin_node: Node identifier stamped on bridged events.
        db_queue_maxsize: Ingest queue depth. Publisher events are dropped
            (with rate-limited ``events.dropped.ingest`` notice) when the queue
            is full.
        subscriber_queue_maxsize: Per-subscriber-connection queue depth. Slow
            client overflow emits ``events.dropped.subscribe``.
        drop_notice_interval_sec: Minimum seconds between consecutive
            ``events.dropped.ingest`` emissions.
    """
    effective_db = db_path if persist else ":memory:"
    store = EventStore(effective_db)
    subscriber_queues: set[asyncio.Queue[dict[str, Any]]] = set()
    ingest: IngestServer | None = None
    uds_server: uvicorn.Server | None = None
    tcp_server: uvicorn.Server | None = None
    serve_tasks: list[asyncio.Task[None]] = []
    retention_task: asyncio.Task[None] | None = None
    started = False
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _signal_handler() -> None:
        if stop_event.is_set():
            return
        stop_event.set()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.remove_signal_handler(sig)
            except RuntimeError:
                pass

    try:
        await store.open()
        ingest = IngestServer(
            store,
            ingest_sock,
            subscriber_queues,
            db_queue_maxsize=db_queue_maxsize,
            drop_notice_interval_sec=drop_notice_interval_sec,
        )
        await ingest.start()

        app = create_app(
            store,
            subscriber_queues,
            ingest,
            subscriber_queue_maxsize=subscriber_queue_maxsize,
        )

        query_sock_path = Path(query_sock)
        query_sock_path.parent.mkdir(parents=True, exist_ok=True)
        if query_sock_path.exists():
            query_sock_path.unlink()

        uds_config = uvicorn.Config(
            app, uds=query_sock, log_level="warning", access_log=False
        )
        uds_server = uvicorn.Server(uds_config)
        uds_task = asyncio.create_task(uds_server.serve())
        serve_tasks.append(uds_task)

        await asyncio.sleep(0.1)
        if query_sock_path.exists():
            os.chmod(query_sock, query_sock_mode)

        if tcp_enabled:
            await ingest.start_tcp("0.0.0.0", tcp_ingest_port)
            tcp_config = uvicorn.Config(
                app,
                host="0.0.0.0",
                port=tcp_query_port,
                log_level="warning",
                access_log=False,
            )
            tcp_server = uvicorn.Server(tcp_config)
            tcp_task = asyncio.create_task(tcp_server.serve())
            serve_tasks.append(tcp_task)
            logger.info(
                "TCP developer mode: ingest=:%d, query=:%d",
                tcp_ingest_port,
                tcp_query_port,
            )

        mode_label = "persistent" if persist else "in-memory"
        logger.info(
            "Event service started (%s, ingest=%s, query=%s, db=%s, tcp=%s)",
            mode_label,
            ingest_sock,
            query_sock,
            effective_db,
            tcp_enabled,
        )
        ts_ms, ts_iso = _event_timestamp()
        started_payload: dict[str, Any] = {
            "ingest_sock": ingest_sock,
            "query_sock": query_sock,
            "db_path": effective_db,
            "persist": persist,
        }
        if tcp_enabled:
            started_payload["tcp_ingest_port"] = tcp_ingest_port
            started_payload["tcp_query_port"] = tcp_query_port
        await store.insert_events(
            [
                {
                    "signal": "event.service.started",
                    "role": "coordination",
                    "scope": "global",
                    "ts_unix_ms": ts_ms,
                    "timestamp": ts_iso,
                    "source": "event_service",
                    "payload": started_payload,
                }
            ]
        )
        started = True

        if persist:
            retention_task = asyncio.create_task(
                _retention_loop(
                    store,
                    retention_days=retention_days,
                    max_sessions=max_sessions,
                )
            )

        # Event bridge: forward scope=global events to upstream
        bridge: Any = None
        if bridge_upstream_sock and bridge_origin_node:
            from .bridge import EventBridge

            bridge = EventBridge(
                local_query_sock=query_sock,
                upstream_ingest_sock=bridge_upstream_sock,
                origin_node=bridge_origin_node,
            )
            await bridge.start()

        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, _signal_handler)

        await stop_event.wait()
    except Exception as e:
        logger.critical("Event service lifecycle failure: %s", e, exc_info=True)
    finally:
        logger.info("Event service shutting down...")
        if bridge is not None:
            await bridge.stop()
        if retention_task is not None:
            retention_task.cancel()
            try:
                await retention_task
            except asyncio.CancelledError:
                pass
        if ingest is not None:
            await ingest.stop()
        if uds_server is not None:
            uds_server.should_exit = True
        if tcp_server is not None:
            tcp_server.should_exit = True
        for t in serve_tasks:
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        if started and persist:
            ts_ms, ts_iso = _event_timestamp()
            await store.insert_events(
                [
                    {
                        "signal": "event.service.stopped",
                        "role": "coordination",
                        "scope": "global",
                        "ts_unix_ms": ts_ms,
                        "timestamp": ts_iso,
                        "source": "event_service",
                        "payload": {},
                    }
                ]
            )
        await store.close()
        logger.info("Event service stopped")


async def start_event_service(
    *,
    db: str = "~/.events/events.db",
    sock: str = os.environ.get(
        "EVENTS_INGEST_SOCK", "/tmp/universal-protocol/events.sock"
    ),
    query_sock: str = os.environ.get(
        "EVENTS_QUERY_SOCK", "/tmp/universal-protocol/events-query.sock"
    ),
    host: str | None = None,
    port: int | None = None,
    retention_days: int = 7,
    max_sessions: int = 2,
    persist: bool = True,
    bridge_upstream_sock: str | None = None,
    bridge_origin_node: str | None = None,
    db_queue_maxsize: int = 10000,
    subscriber_queue_maxsize: int = _DEFAULT_SUBSCRIBER_QUEUE_SIZE,
    drop_notice_interval_sec: float = 1.0,
) -> asyncio.Task[None]:
    """Start the event service as a background asyncio task.

    Args:
        persist: When False, uses SQLite :memory: (no disk, no retention).
        bridge_upstream_sock: Forward scope=global events to upstream ingest socket.
        bridge_origin_node: Node identifier stamped on bridged events.
        db_queue_maxsize: Ingest queue depth (see ``run_service`` docstring).
        subscriber_queue_maxsize: Per-subscriber queue depth.
        drop_notice_interval_sec: Min interval between drop-notice emissions.
    """
    db_path = os.path.expanduser(db)
    tcp_enabled = host is not None and port is not None
    task = asyncio.create_task(
        run_service(
            db_path=db_path,
            ingest_sock=sock,
            query_sock=query_sock,
            retention_days=retention_days,
            max_sessions=max_sessions,
            persist=persist,
            tcp_enabled=tcp_enabled,
            tcp_ingest_port=port or 7101,
            tcp_query_port=(port or 7101) + 1,
            bridge_upstream_sock=bridge_upstream_sock,
            bridge_origin_node=bridge_origin_node,
            db_queue_maxsize=db_queue_maxsize,
            subscriber_queue_maxsize=subscriber_queue_maxsize,
            drop_notice_interval_sec=drop_notice_interval_sec,
        )
    )
    return task
