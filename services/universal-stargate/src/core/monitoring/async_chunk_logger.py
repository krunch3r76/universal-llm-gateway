"""
Async chunk logger with background queue for non-blocking monitoring.

This module provides a truly asynchronous way to log streaming chunks
without blocking the main streaming path.
"""

import asyncio
import time
from dataclasses import dataclass

from universal_logging import get_logger

logger = get_logger(__name__)


@dataclass
class ChunkLogEntry:
    """Single chunk log entry"""

    chunk_str: str
    request_id: str
    chunk_number: int
    token_metrics: dict | None
    timestamp: float


class AsyncChunkLogger:
    """
    Non-blocking chunk logger with background worker.

    Features:
    - Chunks are queued instantly (no blocking)
    - Background worker batches chunks for efficiency
    - Bounded queue prevents memory issues
    - Drops chunks if queue full (monitoring must never block streaming)
    """

    def __init__(
        self,
        monitor,
        max_queue_size: int = 1000,
        batch_size: int = 10,
        batch_timeout_ms: float = 100.0,
    ):
        """
        Initialize async chunk logger.

        Args:
            monitor: StargateMonitor instance to send batches to
            max_queue_size: Maximum chunks in queue (drops if exceeded)
            batch_size: Number of chunks per batch
            batch_timeout_ms: Max time to wait for full batch (ms)
        """
        self.monitor = monitor
        self.max_queue_size = max_queue_size
        self.batch_size = batch_size
        self.batch_timeout = batch_timeout_ms / 1000.0  # Convert to seconds

        self.queue: asyncio.Queue = asyncio.Queue(maxsize=max_queue_size)
        self._worker_task: asyncio.Task | None = None
        self._running = False

        # Metrics
        self.stats = {
            "chunks_queued": 0,
            "chunks_dropped": 0,
            "batches_sent": 0,
            "errors": 0,
        }

    async def start(self):
        """Start background worker"""
        if self._running:
            logger.warning("AsyncChunkLogger already running")
            return

        self._running = True
        self._worker_task = asyncio.create_task(self._worker_loop())
        logger.info(
            f"✅ AsyncChunkLogger started (queue={self.max_queue_size}, batch={self.batch_size})"
        )

    async def stop(self):
        """Stop background worker and flush queue"""
        if not self._running:
            return

        self._running = False

        # Cancel worker
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass

        logger.info(f"AsyncChunkLogger stopped - stats: {self.stats}")

    def log_chunk(
        self,
        chunk_str: str,
        request_id: str,
        chunk_number: int = 0,
        token_metrics: dict | None = None,
    ) -> bool:
        """
        Log chunk asynchronously (non-blocking).

        Returns:
            True if queued, False if dropped
        """
        entry = ChunkLogEntry(
            chunk_str=chunk_str,
            request_id=request_id,
            chunk_number=chunk_number,
            token_metrics=token_metrics,
            timestamp=time.time(),
        )

        try:
            # Non-blocking put - raises QueueFull if full
            self.queue.put_nowait(entry)
            self.stats["chunks_queued"] += 1
            return True
        except asyncio.QueueFull:
            # Drop chunk - monitoring must never block streaming
            self.stats["chunks_dropped"] += 1

            # Log warning every 100 drops
            if self.stats["chunks_dropped"] % 100 == 1:
                logger.warning(
                    f"Monitoring queue full - dropped {self.stats['chunks_dropped']} chunks. "
                    f"Consider increasing max_queue_size or batch_size."
                )
            return False

    async def _worker_loop(self):
        """Background worker that batches and sends chunks"""
        logger.debug("AsyncChunkLogger worker started")

        while self._running:
            try:
                batch = await self._collect_batch()

                if batch:
                    await self._send_batch(batch)

            except asyncio.CancelledError:
                logger.debug("Worker cancelled")
                break
            except Exception as e:
                logger.error(f"Worker error: {e}", exc_info=True)
                self.stats["errors"] += 1
                await asyncio.sleep(0.1)  # Brief pause on error

        logger.debug("AsyncChunkLogger worker stopped")

    async def _collect_batch(self) -> list[ChunkLogEntry]:
        """
        Collect a batch of chunks from queue.

        Returns when:
        - batch_size chunks collected
        - batch_timeout expires
        - queue is empty
        """
        batch = []
        deadline = time.time() + self.batch_timeout

        try:
            # Wait for first chunk (with timeout)
            timeout = max(0.001, deadline - time.time())
            first_chunk = await asyncio.wait_for(self.queue.get(), timeout=timeout)
            batch.append(first_chunk)

            # Drain additional chunks up to batch_size (non-blocking)
            while len(batch) < self.batch_size:
                try:
                    chunk = self.queue.get_nowait()
                    batch.append(chunk)
                except asyncio.QueueEmpty:
                    break

        except TimeoutError:
            # No chunks arrived within timeout
            pass

        return batch

    async def _send_batch(self, batch: list[ChunkLogEntry]):
        """Send batch to monitor"""
        if not batch:
            return

        try:
            # Group by request_id for more efficient monitoring
            by_request: dict[str, list[ChunkLogEntry]] = {}
            for entry in batch:
                by_request.setdefault(entry.request_id, []).append(entry)

            # Send batch for each request
            for request_id, entries in by_request.items():
                # Use batch method if available, otherwise individual
                if hasattr(self.monitor, "log_streaming_chunk_batch"):
                    chunks = [e.chunk_str for e in entries]
                    start_chunk_number = entries[0].chunk_number
                    token_metrics = entries[
                        0
                    ].token_metrics  # Use first entry's metrics

                    await self.monitor.log_streaming_chunk_batch(
                        chunks=chunks,
                        start_chunk_number=start_chunk_number,
                        request_id=request_id,
                        token_metrics=token_metrics,
                    )
                else:
                    # Fallback: send individually
                    for entry in entries:
                        await self.monitor.log_streaming_chunk_async(
                            chunk_str=entry.chunk_str,
                            chunk_number=entry.chunk_number,
                            request_id=request_id,
                            token_metrics=entry.token_metrics,
                        )

            self.stats["batches_sent"] += 1
            # logger.debug(f"Sent batch of {len(batch)} chunks")

        except Exception as e:
            logger.error(f"Failed to send batch: {e}")
            self.stats["errors"] += 1

    def get_stats(self) -> dict:
        """Get current statistics"""
        return {
            **self.stats,
            "queue_size": self.queue.qsize(),
            "drop_rate": self.stats["chunks_dropped"]
            / max(1, self.stats["chunks_queued"]),
        }


# Convenience function for integration
async def create_async_chunk_logger(
    monitor, max_queue_size: int = 1000, batch_size: int = 10
) -> AsyncChunkLogger:
    """
    Create and start an AsyncChunkLogger.

    Usage:
        logger = await create_async_chunk_logger(monitor)

        # In streaming loop:
        logger.log_chunk(chunk_str, request_id)  # Non-blocking!

        # On shutdown:
        await logger.stop()
    """
    logger_instance = AsyncChunkLogger(
        monitor=monitor, max_queue_size=max_queue_size, batch_size=batch_size
    )
    await logger_instance.start()
    return logger_instance
