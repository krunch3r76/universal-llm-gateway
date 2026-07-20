"""Async queue processor mixin for non-blocking state channel metrics collection.

Serializes metric mutations through a bounded asyncio queue so WebSocket handlers
never block on aggregation while preserving timeout and shutdown drain semantics.
"""

import asyncio
from collections.abc import Callable

from universal_logging import get_logger

logger = get_logger(__name__)


class MetricQueueProcessor:
    """Mixin providing queued submission and background processing for metrics."""

    _queue: asyncio.Queue | None
    _result_queue: asyncio.Queue | None
    _processor_task: asyncio.Task | None
    _queue_size: int
    _queue_timeout: float

    async def _ensure_processor(self):
        loop = asyncio.get_running_loop()
        if self._queue is None:
            self._queue = asyncio.Queue(maxsize=self._queue_size)
        if self._result_queue is None:
            self._result_queue = asyncio.Queue()
        if self._processor_task is None or self._processor_task.done():
            self._processor_task = loop.create_task(self._process_queue())

    async def _submit(
        self, handler: Callable, *args, expects_result: bool = False, **kwargs
    ):
        """Submit metric operation with error isolation."""
        if not self._queue or not self._result_queue:
            logger.warning(
                f"Metrics queue not initialized, skipping {handler.__name__}"
            )
            return None

        try:
            await self._ensure_processor()
            loop = asyncio.get_running_loop()
            future = loop.create_future()
            operation = (handler, args, kwargs, expects_result, future)

            await asyncio.wait_for(
                self._queue.put(operation), timeout=self._queue_timeout
            )

            if expects_result:
                return await asyncio.wait_for(future, timeout=self._queue_timeout * 2)
            return None

        except TimeoutError:
            logger.warning(f"Metrics queue operation timed out: {handler.__name__}")
            return None
        except Exception as e:
            logger.error(f"Metrics operation failed: {handler.__name__}: {e}")
            return None

    async def _process_queue(self):
        """Process queued metric operations with enhanced reliability."""
        logger.info("Metrics queue processor started")
        consecutive_errors = 0
        loop = asyncio.get_running_loop()
        last_cleanup = loop.time()

        while True:
            try:
                current_time = loop.time()
                if current_time - last_cleanup > 1.0:
                    asyncio.create_task(self._periodic_cleanup())
                    last_cleanup = current_time

                try:
                    queue_task = asyncio.create_task(self._queue.get())
                    done, _pending = await asyncio.wait(
                        {queue_task},
                        timeout=0.001,
                    )

                    if done:
                        handler, args, kwargs, expects_result, future = await queue_task
                        consecutive_errors = 0
                    else:
                        queue_task.cancel()
                        await asyncio.sleep(0)
                        continue
                except asyncio.CancelledError:
                    raise

                if handler is None:
                    break

                try:
                    result = handler(*args, **kwargs)
                    if expects_result and not future.done():
                        future.set_result(result)
                except Exception as e:
                    logger.warning(f"Handler {handler.__name__} failed: {e}")
                    if expects_result and not future.done():
                        future.set_exception(e)
                finally:
                    self._queue.task_done()

            except asyncio.CancelledError:
                logger.info("Metrics queue processor cancelled")
                await self._drain_queue()
                break
            except Exception as e:
                consecutive_errors += 1
                logger.error(f"Queue processor error #{consecutive_errors}: {e}")
                max_consecutive_errors = 10
                if consecutive_errors > max_consecutive_errors:
                    logger.critical("Too many consecutive errors, resetting")
                    await asyncio.sleep(5)
                    consecutive_errors = 0

    async def _periodic_cleanup(self):
        """Perform periodic maintenance tasks."""
        await asyncio.sleep(0)
        self._handle_cleanup_old_channels(3600)

    async def _drain_queue(self):
        """Drain remaining queue operations on shutdown."""
        if not self._queue:
            return

        drained = 0
        while not self._queue.empty():
            try:
                operation = self._queue.get_nowait()
                _handler, _args, _kwargs, _expects_result, future = operation
                if future and not future.done():
                    future.set_exception(RuntimeError("Queue draining during shutdown"))
                self._queue.task_done()
                drained += 1
            except asyncio.QueueEmpty:
                break

        if drained > 0:
            logger.info(f"Drained {drained} operations from metrics queue")
