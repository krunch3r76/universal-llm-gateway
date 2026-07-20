"""System lifecycle event signals.

Signals:
    system.started — Stargate session started; carries pid + role for forensics
    system.shutdown — Stargate shutting down
"""

# ruff: noqa: N802

from universal_event_bus import Event, event_factory

# ========================================
# System Event Signals
# ========================================

SYSTEM_STARTED = "system.started"
"""
System session started.

Payload: {
    "pid": int,              # OS process ID — cross-check against lsof/ps
    "role": str,             # "master" | "edge" | "relay"
    "started_at": float,     # Unix epoch (time.time()) at startup
    "version": str | None,   # Package version string, if available
}
"""

SYSTEM_SHUTDOWN = "system.shutdown"
"""
System shutting down
Payload: {} (empty)
"""


# ========================================
# Factory Functions
# ========================================


@event_factory
def SystemStarted(
    pid: int,
    role: str,
    started_at: float,
    version: str | None = None,
) -> Event:
    """
    Create SYSTEM_STARTED event.

    ∀ stargate session: exactly one system.started emitted at startup.
    pid + started_at together identify the session uniquely in a non-truncated log.

    Args:
        pid: OS process ID (os.getpid())
        role: "master" | "edge" | "relay"
        started_at: Unix epoch at startup (time.time())
        version: Package version string

    Returns:
        Event with SystemStarted signal
    """
    return Event(
        signal=SYSTEM_STARTED,
        payload={
            "pid": pid,
            "role": role,
            "started_at": started_at,
            "version": version,
        },
    )


@event_factory
def SystemShutdown() -> Event:
    """
    Create SYSTEM_SHUTDOWN event.

    Returns:
        Event with SystemShutdown signal
    """
    return Event(signal=SYSTEM_SHUTDOWN, payload={})
