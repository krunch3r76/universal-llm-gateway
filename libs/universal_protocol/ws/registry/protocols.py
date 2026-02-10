"""Registry protocols - explicit contracts for mixin dependencies.

Defines what each registry mixin requires from its host class.
Each mixin has its own protocol, eliminating underspecified contracts.

Invariant: ∀ mixin: self annotation uses mixin-specific protocol
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from universal_protocol.ws.registry.entries import StreamEntry


@runtime_checkable
class CleanupHostProtocol(Protocol):
    """Protocol for RegistryCleanupMixin host.

    Minimal contract: entries access + unregister capability.
    """

    _entries: dict[str, StreamEntry]

    def unregister(self, entry_id: str) -> StreamEntry | None:
        """Remove entry from registry.

        Inputs:
            entry_id: Entry to unregister

        Outputs:
            The removed entry, or None if not found
        """
        ...


@runtime_checkable
class ControlHostProtocol(Protocol):
    """Protocol for RegistryControlMixin host.

    Minimal contract: entries access only.
    All helper methods (_signal_cancel, _try_push_frame) stay on mixin.
    """

    _entries: dict[str, StreamEntry]


@runtime_checkable
class IdleMonitorHostProtocol(Protocol):
    """Protocol for IdleMonitorMixin host.

    Requires: entries, idle config, task storage, and notify method.
    notify_idle_timeout is provided by RegistryControlMixin (diamond inheritance).
    """

    _entries: dict[str, StreamEntry]
    _idle_monitor_task: asyncio.Task[None] | None
    _idle_timeout: float
    _idle_check_interval: float

    def notify_idle_timeout(self, entry_id: str, idle_seconds: float) -> bool:
        """Notify consumer of idle timeout.

        Inputs:
            entry_id: Stream/request identifier
            idle_seconds: Time idle in seconds

        Outputs:
            True if entry found and notified, False if not found
        """
        ...
