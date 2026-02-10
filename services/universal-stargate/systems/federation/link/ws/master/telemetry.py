"""
Telemetry receiver for Master mode.

DEPRECATED: Use systems.federation.telemetry.receiver instead.
This module re-exports for backward compatibility.
"""

from ..telemetry.receiver import MasterTelemetryReceiver

__all__ = ["MasterTelemetryReceiver"]
