"""
HTTP polling mode validation.

Isolated module to avoid circular imports.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...common.config.schema import RemoteStargateConfig


def require_polling_mode(remote_config: RemoteStargateConfig) -> None:
    """
    Verify polling mode is explicitly enabled for this remote.

    Called by factory and constructor, NOT at import time.

    Raises:
        ValueError: If disable_websocket is not true for this remote
    """
    if not remote_config.disable_websocket:
        msg = (
            f"HTTP polling transport requires disable_websocket=true "
            f"for remote '{remote_config.stargate_id}'. "
            f"Use WebSocket transport for event-driven telemetry."
        )
        raise ValueError(msg)
