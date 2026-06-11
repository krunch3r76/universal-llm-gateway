"""Agent-bus lifecycle event factories and emit helpers."""

from .lifecycle import (
    AgentBusDispatchAdmitFailed,
    AgentBusThreadAbandoned,
    AgentBusThreadLifecycleTransitioned,
    AgentBusThreadReopened,
    emit_dispatch_admit_failed,
    emit_dispatch_orphaned,
    emit_lifecycle_transitioned,
    emit_thread_reopened,
)

__all__ = [
    "AgentBusDispatchAdmitFailed",
    "AgentBusThreadAbandoned",
    "AgentBusThreadLifecycleTransitioned",
    "AgentBusThreadReopened",
    "emit_dispatch_admit_failed",
    "emit_dispatch_orphaned",
    "emit_lifecycle_transitioned",
    "emit_thread_reopened",
]
