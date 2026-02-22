"""Non-streaming client disconnect detection and cancellation.

Races inference against a disconnect poll. If the client disconnects first,
sends cancel_inference RPC → cancellation_event set → engine cancels → slot freed.
"""

import asyncio
from collections.abc import Coroutine

from fastapi import Request
from universal_logging import get_logger

logger = get_logger(__name__)


async def cancel_non_streaming_inference(
    worker_controller,
    model_id: str,
    request_id: str,
) -> None:
    """Send cancel_inference RPC to free the Worker slot.

    Shielded: must complete even if outer task is cancelled.
    """
    try:
        await asyncio.shield(
            worker_controller.cancel_work(model_id, stream_id=request_id)
        )
        logger.info(f"[{request_id}] Sent cancellation for {model_id}")
    except asyncio.CancelledError:
        logger.info(f"[{request_id}] Cancellation RPC interrupted but was shielded")
    except Exception as e:
        logger.warning(f"[{request_id}] Failed to cancel inference for {model_id}: {e}")


async def inference_with_disconnect_watch(
    inference_coro: Coroutine,
    request: Request,
    worker_controller,
    model_id: str,
    request_id: str,
) -> dict:
    """Run inference with concurrent client disconnect detection.

    ∀ client_disconnect during non-streaming inference:
      cancel_inference RPC sent → cancellation_event set → engine cancels → slot freed
    """

    async def _poll_disconnect() -> None:
        while not await request.is_disconnected():
            await asyncio.sleep(0.5)

    inference_task = asyncio.create_task(inference_coro)
    disconnect_task = asyncio.create_task(_poll_disconnect())

    try:
        done, pending = await asyncio.wait(
            [inference_task, disconnect_task],
            return_when=asyncio.FIRST_COMPLETED,
        )

        for task in pending:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

        if disconnect_task in done:
            logger.info(
                f"[{request_id}] Client disconnected during non-streaming "
                f"inference for {model_id}"
            )
            await cancel_non_streaming_inference(
                worker_controller, model_id, request_id
            )
            raise asyncio.CancelledError("Client disconnected")

        return inference_task.result()

    except asyncio.CancelledError:
        if not inference_task.done():
            inference_task.cancel()
        await cancel_non_streaming_inference(worker_controller, model_id, request_id)
        raise
