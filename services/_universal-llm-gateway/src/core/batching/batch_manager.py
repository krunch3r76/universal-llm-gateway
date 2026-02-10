"""
Batch Manager for Universal LLM Gateway Phase 2B
Handles batching of multiple inference requests simultaneously.
"""

import asyncio
import time
import uuid
from asyncio import Event
from dataclasses import dataclass, field
from typing import Any

from universal_logging import get_logger

logger = get_logger(__name__)


@dataclass
class BatchRequest:
    """Individual request within a batch"""

    request_id: str
    model_id: str
    messages: list[dict[str, str]]
    max_tokens: int
    temperature: float
    top_p: float
    stop: list[str] | None
    stream: bool
    created_at: float = field(default_factory=time.time)
    completion_event: Event = field(default_factory=Event)
    result: dict[str, Any] | None = None
    error: Exception | None = None


@dataclass
class Batch:
    """Collection of requests to be processed together"""

    batch_id: str
    model_id: str
    requests: list[BatchRequest]
    created_at: float = field(default_factory=time.time)
    max_batch_size: int = 8
    max_wait_time: float = 2.0  # Maximum wait time in seconds


class BatchManager:
    """
    Manages batching of inference requests for improved GPU utilization.

    Features:
    - Groups requests by model for efficient processing
    - Configurable batch sizes and wait times
    - Supports both streaming and non-streaming requests
    - Automatic timeout handling
    """

    def __init__(
        self,
        max_batch_size: int = 8,
        max_wait_time: float = 2.0,
        enable_batching: bool = True,
    ):
        self.max_batch_size = max_batch_size
        self.max_wait_time = max_wait_time
        self.enable_batching = enable_batching

        # Queue for pending requests by model
        self.pending_requests: dict[str, list[BatchRequest]] = {}
        self.batch_processors: dict[str, asyncio.Task] = {}
        self.stats = {
            "total_requests": 0,
            "batched_requests": 0,
            "single_requests": 0,
            "batches_processed": 0,
            "average_batch_size": 0.0,
            "total_wait_time": 0.0,
        }

        logger.info(
            f"BatchManager initialized: max_batch_size={max_batch_size}, "
            f"max_wait_time={max_wait_time}s, enabled={enable_batching}"
        )

    async def process_request(
        self,
        model_id: str,
        messages: list[dict[str, str]],
        max_tokens: int = 512,
        temperature: float = 0.7,
        top_p: float = 0.9,
        stop: list[str] | None = None,
        stream: bool = False,
    ) -> dict[str, Any]:
        """
        Process a single request, potentially batching with others.

        Args:
            model_id: Model to use for inference
            messages: Chat messages
            max_tokens: Maximum tokens to generate
            temperature: Sampling temperature
            top_p: Top-p sampling parameter
            stop: Stop sequences
            stream: Whether to stream response

        Returns:
            Response dictionary
        """
        # Create request
        request = BatchRequest(
            request_id=str(uuid.uuid4()),
            model_id=model_id,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            stop=stop,
            stream=stream,
        )

        self.stats["total_requests"] += 1

        # If batching is disabled, process immediately
        if not self.enable_batching:
            self.stats["single_requests"] += 1
            return await self._process_single_request(request)

        # Add to pending requests for the model
        if model_id not in self.pending_requests:
            self.pending_requests[model_id] = []

        self.pending_requests[model_id].append(request)

        # Start batch processor if not already running
        if model_id not in self.batch_processors:
            self.batch_processors[model_id] = asyncio.create_task(
                self._batch_processor(model_id)
            )

        # Wait for completion
        await request.completion_event.wait()

        if request.error:
            raise request.error

        return request.result

    async def _batch_processor(self, model_id: str):
        """
        Process batches for a specific model.

        Args:
            model_id: Model identifier
        """
        try:
            while True:
                # Wait for requests or timeout
                start_time = time.time()

                while (
                    len(self.pending_requests.get(model_id, [])) < self.max_batch_size
                    and time.time() - start_time < self.max_wait_time
                ):
                    await asyncio.sleep(0.1)

                # Get current requests
                requests = self.pending_requests.get(model_id, [])
                if not requests:
                    # No pending requests, stop processor
                    break

                # Create batch
                batch_requests = requests[: self.max_batch_size]
                self.pending_requests[model_id] = requests[self.max_batch_size :]

                if batch_requests:
                    batch = Batch(
                        batch_id=str(uuid.uuid4()),
                        model_id=model_id,
                        requests=batch_requests,
                        max_batch_size=self.max_batch_size,
                        max_wait_time=self.max_wait_time,
                    )

                    # Process the batch
                    await self._process_batch(batch)

        except Exception as e:
            logger.error(f"Batch processor error for model {model_id}: {e}")
        finally:
            # Cleanup
            if model_id in self.batch_processors:
                del self.batch_processors[model_id]

    async def _process_batch(self, batch: Batch):
        """
        Process a batch of requests.

        Args:
            batch: Batch to process
        """
        try:
            logger.info(
                f"Processing batch {batch.batch_id} with {len(batch.requests)} requests"
            )

            # Check if all requests are for the same model and compatible
            if not self._are_requests_compatible(batch.requests):
                # Fall back to individual processing
                for request in batch.requests:
                    try:
                        request.result = await self._process_single_request(request)
                    except Exception as e:
                        request.error = e
                    finally:
                        request.completion_event.set()
                return

            # Process as batch
            batch_result = await self._execute_batch_inference(batch)

            # Distribute results to individual requests
            for i, request in enumerate(batch.requests):
                if i < len(batch_result):
                    request.result = batch_result[i]
                else:
                    request.error = Exception("Batch processing failed")
                request.completion_event.set()

            # Update statistics
            self.stats["batches_processed"] += 1
            self.stats["batched_requests"] += len(batch.requests)
            wait_time = time.time() - batch.created_at
            self.stats["total_wait_time"] += wait_time

            if self.stats["batches_processed"] > 0:
                self.stats["average_batch_size"] = (
                    self.stats["batched_requests"] / self.stats["batches_processed"]
                )

            logger.info(f"Batch {batch.batch_id} completed in {wait_time:.2f}s")

        except Exception as e:
            logger.error(f"Error processing batch {batch.batch_id}: {e}")

            # Mark all requests as failed
            for request in batch.requests:
                request.error = e
                request.completion_event.set()

    def _are_requests_compatible(self, requests: list[BatchRequest]) -> bool:
        """
        Check if requests can be batched together.

        Args:
            requests: List of requests to check

        Returns:
            True if requests are compatible for batching
        """
        if not requests:
            return False

        # All requests must be for the same model
        model_id = requests[0].model_id
        if not all(req.model_id == model_id for req in requests):
            return False

        # For now, only batch non-streaming requests
        if any(req.stream for req in requests):
            return False

        # Check if parameters are reasonably similar
        # (In a more advanced implementation, you might group by similar parameters)
        return True

    async def _execute_batch_inference(self, batch: Batch) -> list[dict[str, Any]]:
        """
        Execute inference for a batch of requests.

        Args:
            batch: Batch to process

        Returns:
            List of results for each request
        """
        # For now, this is a placeholder - in the real implementation,
        # this would call the model loader with batch processing capabilities
        results = []

        for request in batch.requests:
            # Process each request individually (fallback)
            try:
                result = await self._process_single_request(request)
                results.append(result)
            except Exception as e:
                # Create error response
                results.append(
                    {
                        "error": str(e),
                        "model_id": request.model_id,
                        "request_id": request.request_id,
                    }
                )

        return results

    async def _process_single_request(self, request: BatchRequest) -> dict[str, Any]:
        """
        Process a single request without batching.

        Args:
            request: Request to process

        Returns:
            Response dictionary
        """
        # This would typically call the model manager directly
        # For now, return a placeholder response
        return {
            "id": f"chatcmpl-{request.request_id[:8]}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": request.model_id,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": f"Batch processed response for request {request.request_id}",
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 15, "total_tokens": 25},
        }

    def get_stats(self) -> dict[str, Any]:
        """Get batching statistics"""
        efficiency = 0.0
        if self.stats["total_requests"] > 0:
            efficiency = (
                self.stats["batched_requests"] / self.stats["total_requests"] * 100
            )

        avg_wait_time = 0.0
        if self.stats["batches_processed"] > 0:
            avg_wait_time = (
                self.stats["total_wait_time"] / self.stats["batches_processed"]
            )

        return {
            **self.stats,
            "batching_efficiency_percent": efficiency,
            "average_wait_time_seconds": avg_wait_time,
            "pending_models": list(self.pending_requests.keys()),
            "active_processors": len(self.batch_processors),
        }

    async def shutdown(self):
        """Shutdown the batch manager"""
        logger.info("Shutting down batch manager")

        # Cancel all batch processors
        for task in self.batch_processors.values():
            task.cancel()

        # Wait for cancellation
        if self.batch_processors:
            await asyncio.gather(
                *self.batch_processors.values(), return_exceptions=True
            )

        # Complete any pending requests with error
        for requests in self.pending_requests.values():
            for request in requests:
                request.error = Exception("Batch manager shutdown")
                request.completion_event.set()

        self.pending_requests.clear()
        self.batch_processors.clear()

        logger.info("Batch manager shutdown completed")
