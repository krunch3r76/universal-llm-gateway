"""UDS ingest listener — receives NDJSON events from publishers.

Accepts connections on a Unix Domain Socket. Each publisher streams
newline-delimited JSON events. Events are written to the store via
an asyncio.Queue + single writer task (no locks, no semaphores).
Fan-out to live subscribers happens after DB commit.

StreamReader limit set to 1MB to handle large model output payloads.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from .store import EventStore

logger = logging.getLogger(__name__)

_LINE_LIMIT = 1024 * 1024  # 1MB per line
_BATCH_SIZE = 100
_FLUSH_INTERVAL = 0.25


class IngestServer:
    """UDS listener that ingests NDJSON events into the store."""

    def __init__(
        self,
        store: EventStore,
        socket_path: str,
        subscriber_queues: set[asyncio.Queue[dict[str, Any]]],
    ) -> None:
        self._store = store
        self._socket_path = socket_path
        self._subscriber_queues = subscriber_queues
        self._server: asyncio.Server | None = None
        self._db_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=10000)
        self._writer_task: asyncio.Task[None] | None = None
        self._running = False
        self._events_ingested = 0
        self._events_dropped_publish = 0

    async def start(self) -> None:
        """Bind UDS socket and start the DB writer task."""
        sock_path = Path(self._socket_path)
        if sock_path.exists():
            sock_path.unlink()
        sock_path.parent.mkdir(parents=True, exist_ok=True)

        self._running = True
        self._writer_task = asyncio.create_task(self._db_writer_loop())

        self._server = await asyncio.start_unix_server(
            self._handle_connection,
            path=self._socket_path,
            limit=_LINE_LIMIT,
        )
        os.chmod(self._socket_path, 0o777)
        logger.info("Ingest server listening on %s", self._socket_path)

    async def stop(self) -> None:
        self._running = False
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        if self._writer_task:
            self._writer_task.cancel()
            try:
                await self._writer_task
            except asyncio.CancelledError:
                pass
        sock = Path(self._socket_path)
        if sock.exists():
            sock.unlink()

    async def _handle_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Handle a single publisher connection (NDJSON stream)."""
        peer = writer.get_extra_info("peername", "unknown")
        logger.debug("Publisher connected: %s", peer)
        try:
            while self._running:
                try:
                    line = await reader.readline()
                except asyncio.LimitOverrunError:
                    logger.warning("Line exceeds 1MB limit from %s, skipping", peer)
                    await reader.readuntil(b"\n")
                    continue

                if not line:
                    break
                line_str = line.decode("utf-8", errors="replace").strip()
                if not line_str:
                    continue

                try:
                    event = json.loads(line_str)
                except json.JSONDecodeError:
                    logger.warning("Invalid JSON from %s: %.100s", peer, line_str)
                    continue

                try:
                    self._db_queue.put_nowait(event)
                    self._events_ingested += 1
                except asyncio.QueueFull:
                    self._events_dropped_publish += 1
                    logger.debug("Ingest queue full, dropping event")
        except (ConnectionResetError, BrokenPipeError):
            pass
        except Exception as e:
            logger.warning("Publisher connection error: %s", e)
        finally:
            writer.close()

    async def _db_writer_loop(self) -> None:
        """Drain the queue, batch-insert into SQLite, fan out to subscribers."""
        while self._running or not self._db_queue.empty():
            batch: list[dict[str, Any]] = []
            try:
                event = await asyncio.wait_for(
                    self._db_queue.get(), timeout=_FLUSH_INTERVAL
                )
                batch.append(event)
            except (TimeoutError, asyncio.CancelledError):
                if not self._running and self._db_queue.empty():
                    break
                if not batch:
                    continue

            while len(batch) < _BATCH_SIZE:
                try:
                    batch.append(self._db_queue.get_nowait())
                except asyncio.QueueEmpty:
                    break

            if not batch:
                continue

            snapshots = [
                e
                for e in batch
                if str(e.get("signal", "")).startswith("request.snapshot.")
            ]
            regular = [
                e
                for e in batch
                if not str(e.get("signal", "")).startswith("request.snapshot.")
            ]

            accepted = await self._store.insert_events(regular + snapshots)
            for snap in snapshots:
                payload = snap.get("payload", {})
                await self._store.insert_snapshot(
                    {
                        "request_id": payload.get("request_id", ""),
                        "phase": payload.get("phase", ""),
                        "ts_unix_ms": int(time.time() * 1000),
                        "model_id": payload.get("model_id"),
                        "gateway_id": payload.get("gateway_id"),
                        "payload": payload,
                    }
                )
            for ev in accepted:
                self._fan_out(ev)

    def _fan_out(self, event: dict[str, Any]) -> None:
        """Push event to all live subscriber queues (non-blocking)."""
        dead: list[asyncio.Queue[dict[str, Any]]] = []
        for sq in self._subscriber_queues:
            try:
                sq.put_nowait(event)
            except asyncio.QueueFull:
                try:
                    sq.get_nowait()
                    drop_notice = {
                        "signal": "events.dropped.subscribe",
                        "role": "observation",
                        "scope": "global",
                        "payload": {"count": 1},
                    }
                    sq.put_nowait(drop_notice)
                    sq.put_nowait(event)
                except Exception:
                    dead.append(sq)

        for sq in dead:
            self._subscriber_queues.discard(sq)

    def get_metrics(self) -> dict[str, int]:
        return {
            "events_ingested": self._events_ingested,
            "events_dropped_publish": self._events_dropped_publish,
            "queue_size": self._db_queue.qsize(),
        }
