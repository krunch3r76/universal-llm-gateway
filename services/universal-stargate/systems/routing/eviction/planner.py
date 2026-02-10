"""Model eviction planning and execution."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from model_id import ModelId
from universal_logging import get_logger

from ..selection import Gateway, Placement

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


def calculate_eviction(
    gw: Gateway,
    placement: Placement,
    idle_models: list[str],
) -> tuple[bool, list[str]]:
    """
    Calculate minimal eviction needed to fit placement.

    Returns (can_fit, models_to_evict).

    Strategy:
    1. Check if model already fits without eviction (no-op case)
    2. Calculate minimal eviction to free sufficient resources
    3. Use 10% safety margin for RAM (same as has_enough_ram predicate)

    Note: Returns empty list if no eviction needed. This prevents
    unnecessary evictions, especially for CPU models where multiple
    models can coexist.

    Args:
        gw: Gateway to calculate for
        placement: Model placement requirements
        idle_models: List of idle models available for eviction

    Returns:
        (can_fit, models_to_evict) tuple
    """
    if not idle_models:
        return False, []

    # Default policy: Allow multiple variants of same routing key to coexist
    # They will be evicted naturally when idle and resources are needed
    # Filter out variants of the model we're trying to load from eviction candidates
    target_routing_key = (
        placement.model_id.routing_key
    )  # placement.model_id is already ModelId

    evictable_models = []
    for idle_model in idle_models:
        idle_routing_key = ModelId.parse(idle_model).routing_key
        if idle_routing_key == target_routing_key:
            logger.debug(
                "🛡️ Skipping eviction of '%s' - "
                "same routing key as target '%s' (routing_key='%s'). "
                "Multiple variants allowed to coexist.",
                idle_model,
                placement.model_id,
                target_routing_key,
            )
            # Allow variants of same model to coexist - skip eviction
            continue
        evictable_models.append(idle_model)

    if not evictable_models:
        # All idle models are variants of what we're loading
        logger.debug(
            "✅ No evictable models found - "
            "all idle models are variants of target model (allowed to coexist)"
        )
        return True, []

    # Check if model already fits without eviction
    ram_margin = 1.03  # 3% safety margin for RAM
    ram_needed = int(placement.ram_mb * ram_margin)
    vram_needed = placement.vram_mb

    # Check BOTH resources for hybrid models (vram_mb > 0 AND ram_mb > 0)
    vram_ok = placement.vram_mb == 0 or gw.vram_free_mb >= vram_needed
    ram_ok = placement.ram_mb == 0 or gw.ram_free_mb >= ram_needed

    if vram_ok and ram_ok:
        logger.debug(
            f"✅ Model fits without eviction: "
            f"VRAM {gw.vram_free_mb}MB free >= {vram_needed}MB needed, "
            f"RAM {gw.ram_free_mb}MB free >= {ram_needed}MB needed"
        )
        return True, []  # No eviction needed!

    # Model doesn't fit - eviction required
    # Conservative: evict all (evictable) idle models (no per-model resource data)
    logger.debug(
        f"⚠️ Model doesn't fit, eviction needed: "
        f"VRAM free={gw.vram_free_mb}MB (need {vram_needed}MB), "
        f"RAM free={gw.ram_free_mb}MB (need {ram_needed}MB), "
        f"evicting {len(evictable_models)} models"
    )
    return True, evictable_models
