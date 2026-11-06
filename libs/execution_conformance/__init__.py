"""Phase-4 execution owner conformance — local intent ledger + actuator adapters."""

from .adapters import (
    AgentBusAdapter,
    CdpInflightAdapter,
    CountingAdmitWrapper,
    CursorBridgeBusModelAdapter,
    StargatePipelineAdapter,
)
from .ledger import IntentLedger, IntentRecord
from .manifest import MANIFEST, assert_manifest_complete, manifest_unknowns
from .reconciler import (
    CompensatingAction,
    DispatchResult,
    ReconcileResult,
    compensate_unknown,
    dispatch_intent,
    health_only_would_claim_ready,
    reconcile,
)
from .states import ExecutionState, IllegalStateTransition, transition

__all__ = [
    "AgentBusAdapter",
    "CompensatingAction",
    "CountingAdmitWrapper",
    "CdpInflightAdapter",
    "CursorBridgeBusModelAdapter",
    "DispatchResult",
    "ExecutionState",
    "IllegalStateTransition",
    "IntentLedger",
    "IntentRecord",
    "MANIFEST",
    "ReconcileResult",
    "StargatePipelineAdapter",
    "assert_manifest_complete",
    "compensate_unknown",
    "dispatch_intent",
    "health_only_would_claim_ready",
    "manifest_unknowns",
    "reconcile",
    "transition",
]
