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
from .subscribe import create_subscribe_router

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
) -> FastAPI:
    """Build the FastAPI query/subscribe application."""
    app = FastAPI(title="Event Store")
    query_router = create_query_router(store, ingest, subscriber_queues)
    subscribe_router = create_subscribe_router(store, subscriber_queues)
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
    ingest_sock: str = "/tmp/universal-protocol/events.sock",
    query_sock: str = "/tmp/universal-protocol/events-query.sock",
    retention_days: int = 7,
    max_sessions: int = 2,
    tcp_enabled: bool = False,
    tcp_ingest_port: int = 7101,
    tcp_query_port: int = 7102,
    query_sock_mode: int = _DEFAULT_QUERY_SOCK_MODE,
) -> None:
    """Main service lifecycle - parameterized for library use."""
    store = EventStore(db_path)
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
        ingest = IngestServer(store, ingest_sock, subscriber_queues)
        await ingest.start()

        app = create_app(store, subscriber_queues, ingest)

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

        logger.info(
            "Event service started (ingest=%s, query=%s, db=%s, tcp=%s)",
            ingest_sock,
            query_sock,
            db_path,
            tcp_enabled,
        )
        ts_ms, ts_iso = _event_timestamp()
        started_payload: dict[str, Any] = {
            "ingest_sock": ingest_sock,
            "query_sock": query_sock,
            "db_path": db_path,
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

        retention_task = asyncio.create_task(
            _retention_loop(
                store,
                retention_days=retention_days,
                max_sessions=max_sessions,
            )
        )
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, _signal_handler)

        await stop_event.wait()
    except Exception as e:
        logger.critical("Event service lifecycle failure: %s", e, exc_info=True)
    finally:
        logger.info("Event service shutting down...")
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
        if started:
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
    sock: str = "/tmp/universal-protocol/events.sock",
    query_sock: str = "/tmp/universal-protocol/events-query.sock",
    host: str | None = None,
    port: int | None = None,
    retention_days: int = 7,
    max_sessions: int = 2,
) -> asyncio.Task[None]:
    """Start the event service as a background asyncio task."""
    db_path = os.path.expanduser(db)
    tcp_enabled = host is not None and port is not None
    task = asyncio.create_task(
        run_service(
            db_path=db_path,
            ingest_sock=sock,
            query_sock=query_sock,
            retention_days=retention_days,
            max_sessions=max_sessions,
            tcp_enabled=tcp_enabled,
            tcp_ingest_port=port or 7101,
            tcp_query_port=(port or 7101) + 1,
        )
    )
    return task
