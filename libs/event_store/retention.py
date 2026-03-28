"""Retention policies for ephemeral event roles.

Heartbeat signals are high-frequency telemetry that should only survive
within the current Stargate session. The retention loop deletes them
aggressively at each cycle and at startup.
"""

from __future__ import annotations

HEARTBEAT_SIGNALS: frozenset[str] = frozenset(
    {
        "federation.telemetry.received",
        "gateway.resource.updated",
    }
)
