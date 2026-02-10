"""Stream registry - modular stream state management.

Provides:
- StreamEntry: Typed dataclass for stream/request state
- StreamRegistry: Centralized registry with idempotent cleanup
- Per-mixin protocols: CleanupHostProtocol, ControlHostProtocol, IdleMonitorHostProtocol
- stream_registry: Global singleton instance

Invariants:
  ∀ entry_id: at most one entry exists in registry
  ∀ entry: entry.cancellation_event is never None
  cleanup(id) is idempotent: calling n≥1 times results in id ∉ registry
"""

from typing import final

from .cleanup import RegistryCleanupMixin
from .control import RegistryControlMixin
from .entries import EntryKind, StreamEntry, StreamRegistry
from .idle_monitor import IdleMonitorMixin
from .protocols import (
    CleanupHostProtocol,
    ControlHostProtocol,
    IdleMonitorHostProtocol,
)


@final
class FullStreamRegistry(
    StreamRegistry,
    RegistryControlMixin,
    RegistryCleanupMixin,
    IdleMonitorMixin,
):
    """Complete StreamRegistry with all capabilities.

    Combines:
    - StreamRegistry: Entry management (register, get, unregister)
    - RegistryControlMixin: Control plane (cancel_entry, notify_*)
    - RegistryCleanupMixin: Lifecycle teardown (cleanup_entry, cleanup_all)
    - IdleMonitorMixin: Idle monitoring (start/stop idle monitor)

    Invariant: Single instance per process (stream_registry singleton).
    Contract: Satisfies all mixin protocols.

    Note: Marked @final - do not subclass. Create new mixins instead.
    """

    __slots__ = ()  # All slots defined in base classes


# Global singleton
stream_registry = FullStreamRegistry()

__all__ = [
    # Core types
    "EntryKind",
    "StreamEntry",
    "StreamRegistry",
    # Mixins
    "RegistryCleanupMixin",
    "RegistryControlMixin",
    "IdleMonitorMixin",
    # Protocols (per-mixin)
    "CleanupHostProtocol",
    "ControlHostProtocol",
    "IdleMonitorHostProtocol",
    # Composed registry
    "FullStreamRegistry",
    "stream_registry",
]
