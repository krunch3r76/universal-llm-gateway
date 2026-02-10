"""
Batch Scheduler for Universal LLM Gateway Phase 2B
Handles scheduling and prioritization of batched requests.

Event-Driven Design: Each request manages its own timeout via asyncio task.
Expired requests are filtered out naturally during dequeue - no background
scanning needed. Lock-free: single-threaded async, no await in heap ops.
"""

import asyncio
import heapq
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from universal_logging import get_logger

logger = get_logger(__name__)


class RequestPriority(Enum):
    """Request priority levels"""

    LOW = 1
    NORMAL = 2
    HIGH = 3
    URGENT = 4


@dataclass
class ScheduledRequest:
    """
    Request with scheduling information.

    Event-driven timeout: Each request has its own timeout task that fires
    when the request expires. No central cleanup needed.
    """

    priority: RequestPriority
    created_at: float
    model_id: str
    estimated_tokens: int
    timeout: float
    request_data: dict[str, Any]
    timeout_task: asyncio.Task | None = field(default=None, compare=False)

    def __lt__(self, other):
        """For priority queue ordering (higher priority first, then oldest first)"""
        if self.priority != other.priority:
            return self.priority.value > other.priority.value
        return self.created_at < other.created_at

    def is_expired(self) -> bool:
        """Check if request has expired"""
        return time.time() - self.created_at > self.timeout

    def cancel_timeout(self) -> None:
        """Cancel timeout task if it exists"""
        if self.timeout_task and not self.timeout_task.done():
            self.timeout_task.cancel()


class BatchScheduler:
    """
    Intelligent scheduler for batched requests.

    Features:
    - Priority-based scheduling
    - Model-aware batching
    - Resource optimization
    - Timeout handling
    - Load balancing across models

    Thread Safety: Not needed. Single-threaded async with no await in
    critical sections. Uses inline opportunistic cleanup instead of
    background task to avoid concurrent heap modification.
    """

    def __init__(
        self,
        max_queue_size: int = 1000,
        default_timeout: float = 30.0,
        enable_priority_scheduling: bool = True,
    ):
        self.max_queue_size = max_queue_size
        self.default_timeout = default_timeout
        self.enable_priority_scheduling = enable_priority_scheduling

        # Priority queues per model
        self.model_queues: dict[str, list[ScheduledRequest]] = {}

        # Scheduling statistics
        self.stats = {
            "total_scheduled": 0,
            "priority_bypasses": 0,
            "timeouts": 0,
            "queue_full_rejections": 0,
            "average_queue_time": 0.0,
            "model_load_distribution": {},
        }

        logger.info(
            f"BatchScheduler initialized: max_queue_size={max_queue_size}, "
            f"default_timeout={default_timeout}s, "
            f"priority_scheduling={enable_priority_scheduling}"
        )

    async def schedule_request(
        self,
        model_id: str,
        request_data: dict[str, Any],
        priority: RequestPriority = RequestPriority.NORMAL,
        estimated_tokens: int | None = None,
        timeout: float | None = None,
    ) -> bool:
        """
        Schedule a request for processing.

        Event-driven timeout: Each request gets its own timeout task.
        No central cleanup needed - expired requests filtered at dequeue.

        Args:
            model_id: Target model identifier
            request_data: Request payload
            priority: Request priority level
            estimated_tokens: Estimated token count for resource planning
            timeout: Request timeout (uses default if None)

        Returns:
            True if successfully scheduled, False if queue is full
        """
        # Check queue capacity
        current_queue_size = sum(len(queue) for queue in self.model_queues.values())
        if current_queue_size >= self.max_queue_size:
            self.stats["queue_full_rejections"] += 1
            logger.warning(f"Queue full, rejecting request for model {model_id}")
            return False

        # Create scheduled request
        request_timeout = timeout or self.default_timeout
        scheduled_request = ScheduledRequest(
            priority=priority,
            created_at=time.time(),
            model_id=model_id,
            estimated_tokens=estimated_tokens or self._estimate_tokens(request_data),
            timeout=request_timeout,
            request_data=request_data,
        )

        # Create timeout task for this request (event-driven expiration)
        scheduled_request.timeout_task = asyncio.create_task(
            self._handle_request_timeout(scheduled_request)
        )

        # Initialize model queue if needed
        if model_id not in self.model_queues:
            self.model_queues[model_id] = []

        # Add to queue with priority ordering
        if self.enable_priority_scheduling:
            heapq.heappush(self.model_queues[model_id], scheduled_request)
        else:
            self.model_queues[model_id].append(scheduled_request)

        # Update statistics
        self.stats["total_scheduled"] += 1
        if model_id not in self.stats["model_load_distribution"]:
            self.stats["model_load_distribution"][model_id] = 0
        self.stats["model_load_distribution"][model_id] += 1

        logger.debug(f"Scheduled {priority.name} priority request for model {model_id}")
        return True

    async def _handle_request_timeout(self, request: ScheduledRequest) -> None:
        """
        Event-driven timeout handler for individual requests.

        Waits for timeout duration, then logs (queue cleanup happens at dequeue).
        """
        try:
            await asyncio.sleep(request.timeout)
            # Request expired - will be filtered out during get_next_batch()
            logger.debug(
                f"Request timeout fired for model {request.model_id} "
                f"(will be filtered at dequeue)"
            )
        except asyncio.CancelledError:
            # Request was processed before timeout - normal case
            pass

    async def get_next_batch(
        self, model_id: str, max_batch_size: int = 8, max_tokens_per_batch: int = 4096
    ) -> list[dict[str, Any]]:
        """
        Get the next batch of requests for a model.

        Event-driven: Expired requests are naturally filtered here (each request
        manages its own timeout task, no scanning needed).

        Args:
            model_id: Model identifier
            max_batch_size: Maximum number of requests in batch
            max_tokens_per_batch: Maximum total tokens in batch

        Returns:
            List of request data for the batch
        """
        if model_id not in self.model_queues:
            return []

        batch: list[dict[str, Any]] = []
        batch_requests: list[ScheduledRequest] = []
        total_tokens = 0
        queue = self.model_queues[model_id]

        # Get requests for batch
        while (
            len(batch) < max_batch_size
            and queue
            and total_tokens < max_tokens_per_batch
        ):
            if self.enable_priority_scheduling:
                request = heapq.heappop(queue)
            else:
                request = queue.pop(0)

            # Check if request has expired (event-driven timeout already fired)
            if request.is_expired():
                self.stats["timeouts"] += 1
                request.cancel_timeout()  # Clean up timeout task
                logger.warning(f"Request timed out for model {model_id}")
                continue

            # Check token limit
            if total_tokens + request.estimated_tokens > max_tokens_per_batch:
                # Put request back if it would exceed token limit
                if self.enable_priority_scheduling:
                    heapq.heappush(queue, request)
                else:
                    queue.insert(0, request)
                break

            # Add to batch and cancel timeout (request being processed)
            batch.append(request.request_data)
            batch_requests.append(request)
            total_tokens += request.estimated_tokens
            request.cancel_timeout()

        if batch:
            # Calculate average queue time
            current_time = time.time()
            total_queue_time = sum(
                current_time - req.created_at for req in batch_requests
            )
            avg_queue_time = total_queue_time / len(batch)

            # Update running average
            if self.stats["total_scheduled"] > 0:
                self.stats["average_queue_time"] = (
                    self.stats["average_queue_time"]
                    * (self.stats["total_scheduled"] - len(batch))
                    + total_queue_time
                ) / self.stats["total_scheduled"]

            logger.info(
                f"Created batch of {len(batch)} requests for model {model_id}, "
                f"total tokens: {total_tokens}, avg queue time: {avg_queue_time:.2f}s"
            )

        return batch

    def _estimate_tokens(self, request_data: dict[str, Any]) -> int:
        """
        Estimate token count for a request.

        Args:
            request_data: Request payload

        Returns:
            Estimated token count
        """
        # Simple estimation based on message content and max_tokens
        messages = request_data.get("messages", [])
        content_length = sum(len(msg.get("content", "")) for msg in messages)
        prompt_tokens = content_length // 4  # Rough estimation: 4 chars per token

        max_tokens = request_data.get("max_tokens", 512)
        return prompt_tokens + max_tokens

    def get_queue_status(self) -> dict[str, Any]:
        """Get current queue status"""
        queue_sizes = {}
        total_queue_size = 0

        for model_id, queue in self.model_queues.items():
            queue_size = len(queue)
            queue_sizes[model_id] = queue_size
            total_queue_size += queue_size

        return {
            "total_queued_requests": total_queue_size,
            "queue_sizes_by_model": queue_sizes,
            "max_queue_size": self.max_queue_size,
            "queue_utilization_percent": (total_queue_size / self.max_queue_size * 100)
            if self.max_queue_size > 0
            else 0,
        }

    def get_stats(self) -> dict[str, Any]:
        """Get scheduling statistics"""
        queue_status = self.get_queue_status()

        return {
            **self.stats,
            **queue_status,
            "models_active": len(self.model_queues),
            "priority_scheduling_enabled": self.enable_priority_scheduling,
        }

    async def shutdown(self):
        """
        Shutdown the scheduler.

        Event-driven cleanup: Cancel all pending timeout tasks for queued requests.
        """
        logger.info("Shutting down batch scheduler")

        # Cancel all timeout tasks for pending requests
        timeout_tasks = []
        for queue in self.model_queues.values():
            for request in queue:
                if request.timeout_task and not request.timeout_task.done():
                    request.timeout_task.cancel()
                    timeout_tasks.append(request.timeout_task)

        # Wait for cancellations to complete
        if timeout_tasks:
            await asyncio.gather(*timeout_tasks, return_exceptions=True)
            logger.debug(f"Cancelled {len(timeout_tasks)} timeout tasks")

        # Clear all queues
        self.model_queues.clear()

        logger.info("Batch scheduler shutdown completed")
