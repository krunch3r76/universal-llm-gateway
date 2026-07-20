"""Debug event emission for gateway model load handshake tracing."""

from typing import Any

from universal_event_bus.events.debug import emit_debug_event


async def emit_gateway_load_handshake_debug(
    step: str,
    model_id: str,
    correlation_id: str | None,
    **extra: Any,
) -> None:
    """Emit debug.gateway.load.handshake events tracing gateway-to-worker load RPC steps."""
    await emit_debug_event(
        "debug.gateway.load.handshake",
        {
            "step": step,
            "model_id": model_id,
            "correlation_id": correlation_id,
            **extra,
        },
        source="gateway",
    )
