"""Phase-4 execution owner conformance — local intent ledger + actuator adapters."""

from .adapters import (
    AgentBusAdapter,
    CdpInflightAdapter,
    CountingAdmitWrapper,
    CursorBridgeBusModelAdapter,
    HealthOnlyAdapter,
    StargatePipelineAdapter,
)
from .ledger import (
    IntentRecord,
    IntentStore,
    ReferenceSqliteIntentLedger,
    ReserveOutcome,
)
from .manifest import MANIFEST, assert_manifest_complete, manifest_unknowns
from .reconciler import (
    CompensatingAction,
    DispatchResult,
    ReconcileResult,
    compensate_unknown,
    dispatch_intent,
    reconcile,
    resolve_ref_conflict,
)
from .states import ExecutionState, IllegalStateTransitionError

__all__ = [
    "AgentBusAdapter",
    "CompensatingAction",
    "CountingAdmitWrapper",
    "CdpInflightAdapter",
    "CursorBridgeBusModelAdapter",
    "DispatchResult",
    "ExecutionState",
    "HealthOnlyAdapter",
    "IllegalStateTransitionError",
    "IntentRecord",
    "IntentStore",
    "ReferenceSqliteIntentLedger",
    "MANIFEST",
    "ReconcileResult",
    "ReserveOutcome",
    "StargatePipelineAdapter",
    "assert_manifest_complete",
    "compensate_unknown",
    "dispatch_intent",
    "manifest_unknowns",
    "reconcile",
    "resolve_ref_conflict",
]
