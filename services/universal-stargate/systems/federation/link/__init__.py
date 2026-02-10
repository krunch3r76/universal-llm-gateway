"""
Telemetry transport layer for Master↔Remote communication.

SCOPE: Telemetry transport ONLY.
- Inference forwarding: routing/forward.py (HTTP)
- Token counting: api/tokens.py (HTTP)
- Model load commands: orchestration/ (HTTP)

Transport is selected per-remote via config.disable_websocket.
"""

from .protocol import (
    MixedFleetTelemetryManager,
    TelemetryEmitter,
    TelemetryReceiver,
    TelemetryUpdate,
)

__all__ = [
    "MixedFleetTelemetryManager",
    "TelemetryEmitter",
    "TelemetryReceiver",
    "TelemetryUpdate",
]
