"""Model eviction planning and execution."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from universal_logging import get_logger

from ..selection import Gateway

if TYPE_CHECKING:
    from gateways.types import GatewayInstance

logger = get_logger(__name__)


async def unload_models(
    load_waiter,
    gateway_instance: GatewayInstance,
    models_to_evict: list[str],
) -> bool:
    """
    Unload models from gateway and WAIT for confirmation.

    Event-driven flow:
    1. Send DELETE request with force=True
    2. Wait for MODEL_UNLOADED event (via load_waiter)
    3. Only return success when event confirms resources freed

    Timeout: 10s for force unload (process killed in ~5s max)

    Args:
        load_waiter: Load waiter for event confirmation
        gateway_instance: Gateway to unload from
        models_to_evict: List of model IDs to evict

    Returns:
        True if at least one model was successfully unloaded
    """
    if not models_to_evict:
        return False

    gateway_name = gateway_instance.config.name

    # Force eviction timeout - process should be killed within 5s
    force_unload_timeout = 10.0

    async def unload_and_wait(model_id: str) -> tuple[str, bool, str | None]:
        """
        Unload single model with force and wait for event confirmation.

        Returns (model_id, success, error_msg)
        """
        try:
            logger.debug(f"⚡ Force evicting {model_id} from {gateway_name}")

            # Try normal unload first
            try:
                initiated = await gateway_instance.client.unload_model(
                    model_id, force=True
                )
            except Exception as e:
                logger.warning(f"⚠️ Unload request failed for {model_id}: {e}")
                initiated = False

            if not initiated:
                # Normal unload failed - use force cleanup API
                logger.warning(
                    f"🔧 Model {model_id} unload failed, using force cleanup API"
                )
                try:
                    cleanup_result = (
                        await gateway_instance.client.force_cleanup_process(model_id)
                    )
                    if cleanup_result and cleanup_result.get("status") in (
                        "cleaned",
                        "no_process",
                    ):
                        logger.info(f"✅ Force cleanup succeeded for {model_id}")
                        return model_id, True, None
                except Exception as cleanup_error:
                    logger.warning(f"Force cleanup also failed: {cleanup_error}")
                return model_id, False, "unload failed and cleanup failed"

            # Wait for MODEL_UNLOADED event to confirm resources freed
            if load_waiter:
                from systems.proxy.core.control_plane.model_lifecycle.waiting import (
                    handles,
                )

                logger.debug(
                    f"⏳ Waiting for MODEL_UNLOADED event for {model_id} "
                    f"on {gateway_name}"
                )

                result = await load_waiter.wait_for_unload(
                    gateway_name, model_id, timeout=force_unload_timeout
                )

                if result == handles.UnloadResult.UNLOADED:
                    logger.info(f"✅ Force evicted {model_id} (event confirmed)")
                    return model_id, True, None
                elif result == handles.UnloadResult.TIMEOUT:
                    logger.warning(
                        f"⏰ Timeout waiting for {model_id} unload event "
                        f"(may still complete)"
                    )
                    return model_id, False, "event timeout"
                else:
                    return model_id, False, f"unexpected result: {result}"
            else:
                # No load_waiter - fall back to assuming success
                # (less reliable but maintains backward compatibility)
                logger.warning(
                    f"No load_waiter available, assuming {model_id} unload succeeded"
                )
                return model_id, True, None

        except Exception as e:
            return model_id, False, str(e)

    # Parallel unload all models with event waiting
    results = await asyncio.gather(
        *(unload_and_wait(m) for m in models_to_evict),
        return_exceptions=True,
    )

    successes = 0
    failures = 0

    for result in results:
        if isinstance(result, Exception):
            failures += 1
            logger.warning(f"Unload exception: {result}")
            continue

        model_id, success, error = result
        if success:
            successes += 1
        else:
            failures += 1
            logger.warning(f"Failed to evict {model_id}: {error}")

    if failures > 0:
        logger.warning(
            f"Eviction: {successes} succeeded, {failures} failed on {gateway_name}"
        )

    return successes > 0


def get_idle_models(gw: Gateway) -> list[str]:
    """
    Get list of idle model IDs on gateway (FIFO eviction order).

    Note: Simplified to FIFO since eviction is rare (P3 path).
    WebSocket provides loaded/busy state - no HTTP fetch needed.
    Resource usage comes from model profiles, not runtime.

    Args:
        gw: Gateway to query

    Returns:
        List of idle model IDs
    """
    # WebSocket-only: idle = loaded - busy
    return [mid for mid in gw.loaded_models if mid not in gw.busy_models]
