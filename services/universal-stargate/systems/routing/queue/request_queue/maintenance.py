import asyncio


class RequestQueueMaintenance:
    """Non-critical maintenance operations (shutdown)."""

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
