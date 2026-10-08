"""Event store server - UDS ingest + FastAPI/uvicorn query server.

Automatic retention reads ``EVENTS_RETENTION_STARTUP`` (``dry_run``, ``run``,
or ``off``; default ``dry_run``). ``dry_run`` logs each table's would-delete
count and does not delete. ``off`` skips the automatic pass. ``run`` deletes.
The daily loop uses the same gate, so a restart does not start the multi-million
row delete. One live pass is ``POST /api/v1/retention/run`` on the query
socket; that route ignores the gate and runs age retention before the session
pass, then a TRUNCATE checkpoint.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI

from .ingest import IngestServer
from .query import create_query_router
from .query_path_health import run_event_loop_lag_probe
from .store import EventStore
from .subscribe import (
    _DEFAULT_SUBSCRIBER_QUEUE_SIZE,
    LiveSubscribers,
    create_subscribe_router,
)

logger = logging.getLogger(__name__)

_SECONDS_PER_DAY = 86400
_DEFAULT_QUERY_SOCK_MODE = 0o660
_GRACEFUL_TIMEOUT_ENV = "EVENT_STORE_GRACEFUL_TIMEOUT_S"
# Query peers (subscribe websockets, idle TCP) must not hold SIGTERM.
_QUERY_SHUTDOWN_WAIT_S = 1.0
_SUBSCRIBER_SHUTDOWN_WAIT_S = 1.0


def _graceful_shutdown_timeout_kwargs() -> dict[str, int]:
    """Return ``timeout_graceful_shutdown`` for uvicorn when env is a finite int.

    When ``EVENT_STORE_GRACEFUL_TIMEOUT_S`` is unset or not a base-10 integer,
    returns an empty dict so uvicorn keeps its default graceful-shutdown behavior.
    """
    raw = os.environ.get(_GRACEFUL_TIMEOUT_ENV)
    if raw is None or raw == "":
        return {}
    try:
        timeout_s = int(raw, 10)
    except ValueError:
        return {}
    return {"timeout_graceful_shutdown": timeout_s}


def _uvicorn_query_config_kwargs() -> dict[str, Any]:
    """Shared uvicorn.Config kwargs for event-store UDS and TCP query servers."""
    return {
        "log_level": "warning",
        "access_log": False,
        **_graceful_shutdown_timeout_kwargs(),
    }


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
    retention_runner: Any = None,
    live_subscribers: LiveSubscribers | None = None,
) -> FastAPI:
    """Build the FastAPI query and subscribe application.

    ``live_subscribers`` is the registry ``run_service`` closes on SIGTERM
    so subscribe sockets are dropped without waiting for the peer.
    """

    @asynccontextmanager
    async def _lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
        lag_task = asyncio.create_task(run_event_loop_lag_probe())
        app.state.event_loop_lag_probe_task = lag_task
        try:
            yield
        finally:
            lag_task.cancel()
            try:
                await lag_task
            except asyncio.CancelledError:
                pass

    app = FastAPI(title="Event Store", lifespan=_lifespan)
    query_router = create_query_router(
        store, ingest, subscriber_queues, retention_runner=retention_runner
    )
    subscribe_router = create_subscribe_router(
        store,
        subscriber_queues,
        subscriber_queue_maxsize=subscriber_queue_maxsize,
        live_subscribers=live_subscribers,
    )
    app.include_router(query_router)
    app.include_router(subscribe_router)
    return app


def retention_startup_mode() -> str:
    """Return ``dry_run``, ``run``, or ``off`` from ``EVENTS_RETENTION_STARTUP``.

    An unset or unknown value is ``dry_run``, so a restart logs counts and
    does not delete.
    """
    raw = os.environ.get("EVENTS_RETENTION_STARTUP", "dry_run").strip().lower()
    if raw in ("dry_run", "run", "off"):
        return raw
    logger.error(
        "EVENTS_RETENTION_STARTUP=%s is not dry_run|run|off; using dry_run",
        raw,
    )
    return "dry_run"


def _log_shutdown_phase(phase: str, started: float) -> None:
    """Log one shutdown phase with elapsed milliseconds for the next SIGKILL."""
    elapsed_ms = int((time.monotonic() - started) * 1000)
    logger.info("shutdown: %s in %d ms", phase, elapsed_ms)


def _drop_query_peers(server: uvicorn.Server | None) -> None:
    """Close accepted query connections so uvicorn does not wait on them.

    ``should_exit`` alone leaves websockets and half-open TCP clients in
    ``server_state.connections`` until each peer goes away. ``shutdown`` on
    the protocol closes the transport from this side.
    """
    if server is None:
        return
    server.should_exit = True
    for conn in list(server.server_state.connections):
        try:
            conn.shutdown()
        except Exception:
            logger.exception("event-store query peer shutdown failed")


async def _finish_query_servers(
    tasks: list[asyncio.Task[None]],
    *,
    timeout: float,
) -> None:
    """Await uvicorn serve tasks, cancelling any that outlive ``timeout``."""
    pending = [task for task in tasks if not task.done()]
    if not pending:
        return
    try:
        await asyncio.wait_for(
            asyncio.gather(*pending, return_exceptions=True),
            timeout=timeout,
        )
    except TimeoutError:
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)


async def run_retention_pass(
    store: EventStore,
    *,
    retention_days: int,
    max_sessions: int,
    dry_run: bool,
) -> dict[str, int]:
    """Delete or count one retention cycle, age cap before the session cap.

    Each step is isolated: a deadline, busy error, or any other exception is
    logged and the remaining steps still run. A writing pass truncates the
    WAL once at the end. ``dry_run`` only counts.
    """
    max_age_ms = retention_days * _SECONDS_PER_DAY * 1000

    async def _step(name: str, awaitable: Any) -> int:
        try:
            deleted = await awaitable
        except Exception:
            logger.exception("Retention step %s failed", name)
            return 0
        return int(deleted or 0)

    debug_deleted = await _step("debug", store.prune_debug_events(dry_run=dry_run))
    heartbeat_deleted = await _step(
        "heartbeat", store.prune_heartbeat_signals(dry_run=dry_run)
    )
    age_deleted = await _step("age", store.run_retention(max_age_ms, dry_run=dry_run))
    session_deleted = await _step(
        "session", store.run_session_retention(max_sessions, dry_run=dry_run)
    )
    if not dry_run:
        try:
            await store.checkpoint_wal_truncate()
        except Exception:
            logger.exception("Retention WAL checkpoint failed")
    logger.info(
        "Retention pass dry_run=%s debug=%d heartbeat=%d age=%d session=%d "
        "(max_sessions=%d, max_days=%d)",
        dry_run,
        debug_deleted,
        heartbeat_deleted,
        age_deleted,
        session_deleted,
        max_sessions,
        retention_days,
    )
    return {
        "debug": debug_deleted,
        "heartbeat": heartbeat_deleted,
        "age": age_deleted,
        "session": session_deleted,
    }


async def _retention_loop(
    store: EventStore,
    *,
    retention_days: int = 7,
    max_sessions: int = 2,
) -> None:
    """Automatic retention. The startup gate decides whether it deletes.

    ``dry_run`` (the default) logs would-delete counts and writes nothing.
    ``run`` deletes. ``off`` skips the pass. The same mode applies to the
    daily iteration. ``POST /api/v1/retention/run`` is the one-shot live pass.
    """
    mode = retention_startup_mode()

    async def _automatic(*, startup: bool) -> None:
        if mode == "off":
            logger.info(
                "Retention automatic pass skipped startup=%s EVENTS_RETENTION_STARTUP=off",
                startup,
            )
            return
        await run_retention_pass(
            store,
            retention_days=retention_days,
            max_sessions=max_sessions,
            dry_run=mode != "run",
        )

    await _automatic(startup=True)
    while True:
        await asyncio.sleep(_SECONDS_PER_DAY)
        await _automatic(startup=False)


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
    live_subscribers = LiveSubscribers()
    ingest: IngestServer | None = None
    uds_server: uvicorn.Server | None = None
    tcp_server: uvicorn.Server | None = None
    bridge: Any = None
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

        async def _operator_retention() -> dict[str, int]:
            return await run_retention_pass(
                store,
                retention_days=retention_days,
                max_sessions=max_sessions,
                dry_run=False,
            )

        app = create_app(
            store,
            subscriber_queues,
            ingest,
            subscriber_queue_maxsize=subscriber_queue_maxsize,
            retention_runner=_operator_retention,
            live_subscribers=live_subscribers,
        )

        query_sock_path = Path(query_sock)
        query_sock_path.parent.mkdir(parents=True, exist_ok=True)
        if query_sock_path.exists():
            query_sock_path.unlink()

        uds_config = uvicorn.Config(
            app, uds=query_sock, **_uvicorn_query_config_kwargs()
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
                **_uvicorn_query_config_kwargs(),
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
        phase = time.monotonic()
        if bridge is not None:
            await bridge.stop()
            _log_shutdown_phase("bridge stopped", phase)
        phase = time.monotonic()
        if retention_task is not None:
            store.stop_background_work()
            retention_task.cancel()
            try:
                await asyncio.wait_for(retention_task, timeout=2.0)
            except (asyncio.CancelledError, TimeoutError):
                pass
            _log_shutdown_phase("retention stopped", phase)
        phase = time.monotonic()
        await live_subscribers.close_all(timeout=_SUBSCRIBER_SHUTDOWN_WAIT_S)
        _log_shutdown_phase("subscribers closed", phase)
        phase = time.monotonic()
        if ingest is not None:
            await ingest.stop()
        _log_shutdown_phase("ingest closed", phase)
        phase = time.monotonic()
        _drop_query_peers(uds_server)
        _drop_query_peers(tcp_server)
        await _finish_query_servers(serve_tasks, timeout=_QUERY_SHUTDOWN_WAIT_S)
        _log_shutdown_phase("query servers stopped", phase)
        phase = time.monotonic()
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
            _log_shutdown_phase("stopped event written", phase)
        phase = time.monotonic()
        await store.close()
        _log_shutdown_phase("store closed", phase)
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
