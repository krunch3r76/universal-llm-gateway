import asyncio
import time
from typing import Any

from universal_logging import get_logger

from .types import QueuedRequest


class RequestQueueRuntime:
    """Runtime request-path operations (enqueue/dequeue/process)."""

    def __init__(
        self,
        max_size: int = 100,
        max_concurrent_processing: int = 100,  # High by default; worker has final say
        default_timeout: float = 300.0,
        event_bus=None,
    ):
        self.max_size = max_size
        self.max_concurrent_processing = max_concurrent_processing
        self.default_timeout = default_timeout
        self.event_bus = event_bus

        self.queue = asyncio.PriorityQueue(maxsize=max_size)
        self.processing_requests: dict[str, QueuedRequest] = {}

        # Metrics
        self.total_enqueued = 0
        self.total_processed = 0
        self.total_timeouts = 0
        self.total_errors = 0
        self.total_requeued = 0  # Track re-queue events
        self.total_removed = 0  # Track client disconnect removals

        self.logger = get_logger(__name__)
        self._shutdown = False

    async def enqueue(
        self, request: dict[str, Any], timeout: float | None = None
    ) -> asyncio.Future:
        """
        Enqueue a request with backpressure protection.

        Returns a Future that resolves to the assigned gateway.
        Raises HTTPException(503) if queue is full.
        """
        if self._shutdown:
            from fastapi import HTTPException

            raise HTTPException(status_code=503, detail="Service is shutting down")

        request_id = f"req_{int(time.time() * 1000)}_{id(request)}"
        model_id = request.get("model", "unknown")

        future = asyncio.Future()
        queued_request = QueuedRequest(
            request_id=request_id,
            request=request,
            model_id=model_id,
            timeout=timeout or self.default_timeout,
            future=future,
        )

        try:
            self.queue.put_nowait(queued_request)
            self.total_enqueued += 1

            future.request_id = request_id  # type: ignore[attr-defined]

            if self.event_bus:
                try:
                    from src.scheduling.events import RequestQueued

                    asyncio.create_task(
                        self.event_bus.publish_async(
                            RequestQueued(
                                request_id=request_id,
                                model_id=model_id,
                                priority=0,
                            )
                        )
                    )
                except Exception as e:
                    self.logger.debug(f"Failed to emit REQUEST_QUEUED event: {e}")

            return future

        except asyncio.QueueFull:
            self.logger.warning(
                f"Queue full ({self.max_size}), rejecting request for model {model_id}"
            )
            from fastapi import HTTPException

            raise HTTPException(
                status_code=503,
                detail=(
                    f"Request queue is full ({self.max_size} requests). "
                    f"Please try again later."
                ),
            )

    async def dequeue(self) -> QueuedRequest | None:
        """Dequeue next request, handling timeouts."""
        try:
            queued_request = self.queue.get_nowait()

            if queued_request.is_expired():
                self.logger.warning(
                    f"Request {queued_request.request_id} timed out after "
                    f"{queued_request.age_seconds():.1f}s"
                )
                self.total_timeouts += 1

                if self.event_bus:
                    try:
                        from src.scheduling.events import RequestTimeout

                        asyncio.create_task(
                            self.event_bus.publish_async(
                                RequestTimeout(
                                    request_id=queued_request.request_id,
                                    gateway_url=None,
                                    model_id=queued_request.model_id,
                                    timeout_seconds=queued_request.timeout,
                                )
                            )
                        )
                    except Exception as e:
                        self.logger.debug(f"Failed to emit REQUEST_TIMEOUT event: {e}")

                if queued_request.future and not queued_request.future.done():
                    age = queued_request.age_seconds()
                    queued_request.future.set_exception(
                        TimeoutError(f"Request timed out after {age:.1f}s")
                    )
                return None

            return queued_request

        except asyncio.QueueEmpty:
            return None

    async def complete_request(self, request_id: str, success: bool = True) -> None:
        """Mark request as completed and remove from processing."""
        if request_id not in self.processing_requests:
            return

        self.processing_requests.pop(request_id, None)
        if success:
            self.total_processed += 1
        else:
            self.total_errors += 1

    async def _safe_requeue(self, request: QueuedRequest) -> None:
        """Re-queue a request (verification failed but not expired)."""
        self.logger.info(
            f"Re-queuing {request.request_id} - gateway resources changed since routing"
        )
        self.total_requeued += 1
        self.processing_requests.pop(request.request_id, None)
        await self.queue.put(request)

    def get_queue_stats(self) -> dict[str, Any]:
        """Get current queue statistics."""
        return {
            "queue_depth": self.queue.qsize(),
            "processing_count": len(self.processing_requests),
            "total_enqueued": self.total_enqueued,
            "total_processed": self.total_processed,
            "total_timeouts": self.total_timeouts,
            "total_errors": self.total_errors,
            "total_requeued": self.total_requeued,
            "total_removed": self.total_removed,
            "max_size": self.max_size,
            "max_concurrent_processing": self.max_concurrent_processing,
        }

    async def process_queue(self, router, max_concurrent: int | None = None) -> None:
        """Process one queued request."""
        max_concurrent = max_concurrent or self.max_concurrent_processing

        queued_request = await self.dequeue()
        if not queued_request:
            return

        try:
            self.logger.info(
                f"Processing queued request {queued_request.request_id} "
                f"for {queued_request.model_id}"
            )

            # CHANGED: route_request returns Gateway | None
            gateway = await router.route_request(queued_request.request)

            if gateway:
                # Post-unification: All gateways are federated
                queued_request.assigned_gateway_name = gateway.name
                self.processing_requests[queued_request.request_id] = queued_request

                # All gateways are federated - trust remote Stargate's resource
                # management.
                self.logger.info(
                    f"Federated gateway {gateway.name} selected for "
                    f"{queued_request.model_id}"
                )
                if queued_request.future and not queued_request.future.done():
                    queued_request.future.set_result(gateway)
                await self.complete_request(queued_request.request_id, success=True)

            elif not queued_request.is_expired():
                self.logger.debug(
                    f"No gateway available for {queued_request.model_id}, re-queuing"
                )
                await self.queue.put(queued_request)
            else:
                if queued_request.future and not queued_request.future.done():
                    queued_request.future.set_exception(
                        TimeoutError("Request timed out while waiting for gateway")
                    )
                self.total_errors += 1

        except Exception as e:
            self.logger.error(
                f"Error processing request {queued_request.request_id}: {e}"
            )
            if queued_request.future and not queued_request.future.done():
                queued_request.future.set_exception(e)
            await self.complete_request(queued_request.request_id, success=False)
