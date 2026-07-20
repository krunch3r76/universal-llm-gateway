"""
Request-queue maintenance mixin: shutdown drain and cancel in-flight work.

Discards queued PriorityQueue items and fails processing Futures with
CancelledError so waiters do not hang across service stop.
"""

import asyncio


class RequestQueueMaintenance:
    """Shutdown drain: empty the queue and cancel in-flight request Futures."""

    async def shutdown(self) -> None:
        """Discard all queued requests on shutdown."""
        self._shutdown = True

        while True:
            try:
                queued_request = self.queue.get_nowait()
                if queued_request.future and not queued_request.future.done():
                    queued_request.future.set_exception(
                        asyncio.CancelledError("Service is shutting down")
                    )
            except asyncio.QueueEmpty:
                break

        for request_id, queued_request in list(self.processing_requests.items()):
            if queued_request.future and not queued_request.future.done():
                queued_request.future.set_exception(
                    asyncio.CancelledError("Service is shutting down")
                )
            await self.complete_request(request_id, success=False)
