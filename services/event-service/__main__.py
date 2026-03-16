"""Event service entry point — UDS ingest + HTTP/WS query server.

Starts two servers:
  1. UDS ingest on /tmp/universal-protocol/events.sock (NDJSON from publishers)
  2. HTTP+WS on /tmp/universal-protocol/events-query.sock (queries + subscriptions)

SQLite database at $EVENT_DB_PATH (default /data/events.db).
Retention runs daily, removing events older than 7 days.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aiohttp import web

from .ingest import IngestServer
from .query import health_handler, metrics_handler, query_handler
from .store import EventStore
from .subscribe import websocket_handler

logger = logging.getLogger(__name__)

_DB_PATH = os.environ.get("EVENT_DB_PATH", "/data/events.db")
_INGEST_SOCK = os.environ.get("UDS_PATH", "/tmp/universal-protocol/events.sock")
_QUERY_SOCK = os.environ.get(
    "QUERY_UDS_PATH", "/tmp/universal-protocol/events-query.sock"
)
_RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", "7"))
_MAX_SESSIONS = int(os.environ.get("MAX_SESSIONS", "2"))
_SECONDS_PER_DAY = 86400
_RETENTION_INTERVAL_S = _SECONDS_PER_DAY


def _event_timestamp() -> tuple[int, str]:
    """Return (unix_ms, ISO8601 Z) timestamp tuple for service events."""
    ts_ms = int(time.time() * 1000)
    ts_iso = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    return ts_ms, ts_iso


async def _retention_loop(store: EventStore) -> None:
    """Apply session-cap retention first, then age retention as safety net.

    Session retention runs immediately on boot so each restart trims older
    sessions without waiting 24 hours. Age-based retention catches stale data
    when few sessions exist across many days.
    """
    max_age_ms = _RETENTION_DAYS * _SECONDS_PER_DAY * 1000
    debug_deleted = await store.prune_debug_events()
    startup_deleted = await store.run_session_retention(_MAX_SESSIONS)
    if debug_deleted or startup_deleted:
        logger.info(
            "Retention (startup): debug=%d session=%d (keeping %d sessions)",
            debug_deleted,
            startup_deleted,
            _MAX_SESSIONS,
        )
    while True:
        await asyncio.sleep(_RETENTION_INTERVAL_S)
        debug_deleted = await store.prune_debug_events()
        session_deleted = await store.run_session_retention(_MAX_SESSIONS)
        age_deleted = await store.run_retention(max_age_ms)
        if debug_deleted or session_deleted or age_deleted:
            logger.info(
                "Retention: debug=%d session=%d age=%d (max_sessions=%d, max_days=%d)",
                debug_deleted,
                session_deleted,
                age_deleted,
                _MAX_SESSIONS,
                _RETENTION_DAYS,
            )


async def run_service() -> None:
    """Main service lifecycle."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    )

    store = EventStore(_DB_PATH)
    subscriber_queues: set[asyncio.Queue[dict[str, Any]]] = set()
    ingest: IngestServer | None = None
    runner: web.AppRunner | None = None
    site: web.UnixSite | None = None
    retention_task: asyncio.Task[None] | None = None
    started = False
    stop_event = asyncio.Event()

    def _signal_handler() -> None:
        stop_event.set()

    try:
        await store.open()
        ingest = IngestServer(store, _INGEST_SOCK, subscriber_queues)
        await ingest.start()

        app = web.Application()
        app["store"] = store
        app["subscriber_queues"] = subscriber_queues
        app["ingest"] = ingest
        app.router.add_post("/v1/query", query_handler)
        app.router.add_get("/v1/subscribe", websocket_handler)
        app.router.add_get("/health", health_handler)
        app.router.add_get("/metrics", metrics_handler)

        query_sock = Path(_QUERY_SOCK)
        if query_sock.exists():
            query_sock.unlink()
        query_sock.parent.mkdir(parents=True, exist_ok=True)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.UnixSite(runner, _QUERY_SOCK)
        await site.start()
        os.chmod(_QUERY_SOCK, 0o777)

        logger.info(
            "Event service started (ingest=%s, query=%s, db=%s)",
            _INGEST_SOCK,
            _QUERY_SOCK,
            _DB_PATH,
        )
        ts_ms, ts_iso = _event_timestamp()
        await store.insert_events(
            [
                {
                    "signal": "event.service.started",
                    "role": "coordination",
                    "scope": "global",
                    "ts_unix_ms": ts_ms,
                    "timestamp": ts_iso,
                    "source": "event_service",
                    "payload": {
                        "ingest_sock": _INGEST_SOCK,
                        "query_sock": _QUERY_SOCK,
                        "db_path": _DB_PATH,
                    },
                }
            ]
        )
        started = True

        retention_task = asyncio.create_task(_retention_loop(store))
        loop = asyncio.get_running_loop()
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
        if site is not None:
            await site.stop()
        if runner is not None:
            await runner.cleanup()
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


def main() -> None:
    asyncio.run(run_service())


if __name__ == "__main__":
    main()
