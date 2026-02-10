import asyncio

from .types import QueuedRequest


class RequestQueueMaintenance:
    """Non-critical maintenance operations (removal, orphan cleanup, shutdown)."""

    async def remove_request(
        self, request_id: str, reason: str = "client_disconnect"
    ) -> bool:
        """
        Remove a request from queue if client disconnected.

        Returns True if removed, False if not found or already processing.
        """
        if request_id in self.processing_requests:
            queued_request = self.processing_requests[request_id]
            self.logger.info(
                f"Cannot remove {request_id}: already processing on "
                f"{queued_request.assigned_gateway_name}"
            )
            return False

        queue_items: list[QueuedRequest] = []
        found = False
        removed_request: QueuedRequest | None = None

        try:
            while not self.queue.empty():
                try:
                    item = self.queue.get_nowait()
                    if item.request_id == request_id:
                        found = True
                        removed_request = item
                        if item.future and not item.future.done():
                            item.future.set_exception(
                                asyncio.CancelledError(f"Request removed: {reason}")
                            )
                    else:
                        queue_items.append(item)
                except asyncio.QueueEmpty:
                    break

            for item in queue_items:
                await self.queue.put(item)

            if found and removed_request:
                self.total_removed += 1
                self.logger.info(
                    f"Removed request {request_id} from queue "
                    f"(reason: {reason}, age: {removed_request.age_seconds():.1f}s)"
                )

                if self.event_bus:
                    try:
                        from src.scheduling.events import RequestRemoved

                        asyncio.create_task(
                            self.event_bus.publish_async(
                                RequestRemoved(
                                    request_id=request_id,
                                    reason=reason,
                                    model_id=removed_request.model_id,
                                    age_seconds=removed_request.age_seconds(),
                                )
                            )
                        )
                    except Exception as e:
                        self.logger.debug(f"Failed to emit REQUEST_REMOVED event: {e}")

            return found

        except Exception as e:
            self.logger.error(f"Error removing request {request_id}: {e}")
            for item in queue_items:
                try:
                    await self.queue.put(item)
                except Exception:
                    pass
            return False

    async def cleanup_orphaned_requests(self, max_age_seconds: float = 60.0) -> int:
        """Remove old requests whose futures were cancelled."""
        queue_items: list[QueuedRequest] = []
        removed_count = 0

        try:
            while not self.queue.empty():
                try:
                    item = self.queue.get_nowait()
                    if (
                        item.age_seconds() > max_age_seconds
                        and item.future
                        and item.future.cancelled()
                    ):
                        removed_count += 1
                        self.total_removed += 1
                        self.logger.warning(
                            f"Cleaned up orphaned request {item.request_id} "
                            f"(age: {item.age_seconds():.1f}s)"
                        )
                    else:
                        queue_items.append(item)
                except asyncio.QueueEmpty:
                    break

            for item in queue_items:
                await self.queue.put(item)

            if removed_count > 0:
                self.logger.info(f"Orphan cleanup removed {removed_count} requests")

            return removed_count

        except Exception as e:
            self.logger.error(f"Error during orphan cleanup: {e}")
            for item in queue_items:
                try:
                    await self.queue.put(item)
                except Exception:
                    pass
            return 0

    async def has_pending_requests_for_model(self, model_id: str) -> bool:
        """
        Return True iff ∃ request ∈ (processing ∪ queued) where
        request.model_id == model_id.
        """
        for req in self.processing_requests.values():
            if req.model_id == model_id:
                return True

        queue_items: list[QueuedRequest] = []
        try:
            while not self.queue.empty():
                try:
                    item = self.queue.get_nowait()
                    queue_items.append(item)
                    if item.model_id == model_id and not item.is_expired():
                        return True
                except asyncio.QueueEmpty:
                    break
        finally:
            for item in queue_items:
                try:
                    self.queue.put_nowait(item)
                except asyncio.QueueFull:
                    self.logger.error("Failed to re-queue item during model check")

        return False

    async def shutdown(self) -> None:
        """Discard all queued requests on shutdown."""
        self._shutdown = True

        while not self.queue.empty():
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
