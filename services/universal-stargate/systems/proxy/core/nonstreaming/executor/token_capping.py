"""
Max-tokens safety cap for per-slot context enforcement.

Part of the `nonstreaming/executor` subpackage. Provides a single free
function that mutates a request body in place when the requested max_tokens
exceeds the effective context available per inference slot (relevant when
parallel_slots > 1 splits the KV cache).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from universal_logging import get_logger

if TYPE_CHECKING:
    from model_id import ModelId

    from systems.federation.common.types import FederatedGateway

logger = get_logger(__name__)


def _cap_max_tokens_to_slot_context(
    request_body: dict[str, Any],
    fed_gateway: FederatedGateway,
    model_id: ModelId,
) -> None:
    """
    Cap max_tokens to effective per-slot context from GATEWAY_SNAPSHOT.

    When parallel_slots > 1, each inference slot has only
    context_length // parallel_slots usable tokens. This function is
    a safety net that fires regardless of whether token counting ran,
    preventing requests that exceed the slot's total context capacity.

    Mutates request_body in place (no-op when metadata is unavailable
    or max_tokens is already within bounds).

    Args:
        request_body: Mutable request dict (may contain max_tokens)
        fed_gateway: Gateway with model_resources from telemetry
        model_id: Target model (ModelId; `.routing_key` used as dict key)
    """
    if not fed_gateway.model_resources:
        return

    model_meta = fed_gateway.model_resources.get(model_id.routing_key)
    if not model_meta:
        return

    effective_ctx = model_meta.get("effective_context_per_slot")
    if not effective_ctx:
        return

    current_max_tokens = request_body.get("max_tokens")
    if current_max_tokens is None:
        return

    if current_max_tokens > effective_ctx:
        logger.info(
            f"Capping max_tokens {current_max_tokens} → {effective_ctx} "
            f"(effective per-slot context for {model_id.routing_key})"
        )
        request_body["max_tokens"] = effective_ctx
