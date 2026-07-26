"""``dispatch_monitor_core`` -- portable Model for the dispatch supervisor monitor.

**Stdlib only.** Zero ``libs.`` / ``services.`` / third-party imports, by contract:
the G5 graft supplies every adapter, and a core that reached into the repo could not
be replayed against fixtures in isolation.

Ownership, per Fable G3 §3.1 (P2 + S5):

* This package: DTOs, three folds, correlation, ``Model.apply`` / ``Model.derive``,
  attention derivation, the projection wire *schema*, and the ``--watch`` sink.
* The G5 ``dispatch_monitor_ulg`` graft: every socket. Event Service subscribes and
  ``resume_from``, the clock, the ``libs/projection`` ``BroadcastHub`` it hosts,
  command RPC, and the click-time ``ReconcilePort``.

The invariant that separates them, and the one to break last::

    derive is a pure function of (folded_state, now_ms). Any datum the View needs
    that the Controller obtained by I/O must enter the Model as an EventRecord.
    The Controller never derives; the Model never does I/O.

See ``README.md`` for the graft handoff, signal-provenance table, and the open
questions G5 must reconcile against the v3 spec.
"""

from __future__ import annotations

from .codec import ProjectionCodec
from .dtos import (
    SCHEMA_VERSION,
    AttentionItem,
    CdpLegRow,
    CharterRootRow,
    HealthProjection,
    PathSimArcRow,
    SdkDispatchRow,
    SupervisorProjection,
    Thresholds,
)
from .model import Model, hints_after_drop
from .protocols import (
    Clock,
    ControllerPort,
    Event,
    EventRecord,
    EventSource,
    ReconcilePort,
)

__all__ = [
    "SCHEMA_VERSION",
    "AttentionItem",
    "CdpLegRow",
    "CharterRootRow",
    "Clock",
    "ControllerPort",
    "Event",
    "EventRecord",
    "EventSource",
    "HealthProjection",
    "Model",
    "PathSimArcRow",
    "ProjectionCodec",
    "ReconcilePort",
    "SdkDispatchRow",
    "SupervisorProjection",
    "Thresholds",
    "hints_after_drop",
]
