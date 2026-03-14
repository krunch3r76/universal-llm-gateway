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
_RETENTION_INTERVAL_S = 86400


async def _retention_loop(store: EventStore) -> None:
    """Apply session-cap retention first, then age retention as safety net.

    Session retention runs immediately on boot so each restart trims older
    sessions without waiting 24 hours. Age-based retention catches stale data
    when few sessions exist across many days.
    """
    max_age_ms = _RETENTION_DAYS * _RETENTION_INTERVAL_S * 1000
    startup_deleted = await store.run_session_retention(_MAX_SESSIONS)
    if startup_deleted:
        logger.info(
            "Session retention (startup): deleted %d rows, keeping %d sessions",
            startup_deleted,
            _MAX_SESSIONS,
        )
    while True:
        await asyncio.sleep(_RETENTION_INTERVAL_S)
        session_deleted = await store.run_session_retention(_MAX_SESSIONS)
        age_deleted = await store.run_retention(max_age_ms)
        if session_deleted or age_deleted:
            logger.info(
                "Retention: session=%d age=%d (max_sessions=%d, max_days=%d)",
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
    try:
        await store.open()
    except Exception as e:
        logger.critical("Failed to open event store at %s: %s", _DB_PATH, e)
        return

    subscriber_queues: set[asyncio.Queue[dict]] = set()

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
    try:
        os.chmod(_QUERY_SOCK, 0o777)
    except OSError as e:
        logger.critical("Failed to set permissions on query socket %s: %s", _QUERY_SOCK, e)
        raise

    logger.info(
        "Event service started (ingest=%s, query=%s, db=%s)",
        _INGEST_SOCK,
        _QUERY_SOCK,
        _DB_PATH,
    )
    ts_ms = int(time.time() * 1000)
    ts_iso = datetime.now(UTC).isoformat().replace("+00:00", "Z")
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

    retention_task = asyncio.create_task(_retention_loop(store))

    stop_event = asyncio.Event()

    def _signal_handler() -> None:
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _signal_handler)

    await stop_event.wait()

    logger.info("Event service shutting down...")
    retention_task.cancel()
    try:
        await retention_task
    except asyncio.CancelledError:
        pass
    await ingest.stop()
    await site.stop()
    await runner.cleanup()
    ts_ms = int(time.time() * 1000)
    ts_iso = datetime.now(UTC).isoformat().replace("+00:00", "Z")
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
