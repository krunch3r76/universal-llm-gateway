"""Routing metrics and decision event signals for Stargate scheduling.

Signal vocabulary is documented in `docs/event-contracts.md`. Implementation is split
across `routing_*.py` modules in this package; this module preserves the historical
`from src.scheduling.events.routing import ...` import surface.
"""

from __future__ import annotations

from .routing_factories_decisions_overflow import (
    ModelCapacityOverflowAssigned,
    ModelLoadOverflowStarted,
    RoutingDecision,
    RoutingDecisionFailed,
    RoutingDequeued,
    RoutingOverflowFailed,
    RoutingOverflowTriggered,
    RoutingQueued,
    RoutingTimeout,
)
from .routing_factories_eviction_hysteresis import (
    EvictionCooldownApplied,
    EvictionCooldownBlocked,
    EvictionDemandApplied,
    RoutingEvictionExecuteFailed,
)
from .routing_factories_eviction_waits_startup import (
    RoutingDrainInitiated,
    RoutingEvictionWaitCancelled,
    RoutingEvictionWaitResolved,
    RoutingEvictionWaitStarted,
    RoutingEvictionWaitTimeout,
    RoutingStartupQueued,
    RoutingStartupResolved,
    RoutingStartupTimeout,
)
from .routing_factories_metrics_load import (
    ModelLoadCompleted,
    ModelLoadInitiated,
    RequestGatewayTrace,
    RequestRouted,
)
from .routing_factories_metrics_tokens import (
    TokenCountCompleted,
    TokenCountingFailed,
    TokenCountPrecondition,
)
from .routing_factories_model_grace import (
    RoutingModelGraceQueued,
    RoutingModelGraceResolved,
    RoutingModelGraceTimeout,
)
from .routing_factories_oom_recovery import (
    ROUTING_INFERENCE_OOM_RECOVERY_STARTED,
    ROUTING_INFERENCE_OOM_RECOVERY_SUCCEEDED,
    OomRecoveryStarted,
    OomRecoverySucceeded,
)
from .routing_signal_constants_decisions import (
    MODEL_CAPACITY_OVERFLOW_ASSIGNED,
    MODEL_LOAD_OVERFLOW_STARTED,
    ROUTING_DECISION,
    ROUTING_DECISION_FAILED,
    ROUTING_DEQUEUED,
    ROUTING_OVERFLOW_FAILED,
    ROUTING_OVERFLOW_TRIGGERED,
    ROUTING_QUEUED,
    ROUTING_TIMEOUT,
)
from .routing_signal_constants_eviction_hysteresis import (
    EVICTION_COOLDOWN_APPLIED,
    EVICTION_COOLDOWN_BLOCKED,
    EVICTION_DEMAND_APPLIED,
    ROUTING_EVICTION_EXECUTE_FAILED,
)
from .routing_signal_constants_metrics import (
    GATEWAY_PHANTOM_MODEL_CLEANED,
    GATEWAY_PHANTOM_MODEL_DETECTED,
    GATEWAY_VRAM_ORPHAN_DETECTED,
    GATEWAY_VRAM_STALENESS_DETECTED,
    MODEL_LOAD_COMPLETED,
    MODEL_LOAD_INITIATED,
    REQUEST_GATEWAY_TRACE,
    REQUEST_ROUTED,
    TOKEN_COUNTING_FAILED,
    TOKEN_COUNT_COMPLETED,
    TOKEN_COUNT_PRECONDITION,
)
from .routing_signal_constants_routing_waits import (
    ROUTING_DRAIN_INITIATED,
    ROUTING_EVICTION_WAIT_CANCELLED,
    ROUTING_EVICTION_WAIT_RESOLVED,
    ROUTING_EVICTION_WAIT_STARTED,
    ROUTING_EVICTION_WAIT_TIMEOUT,
    ROUTING_MODEL_GRACE_QUEUED,
    ROUTING_MODEL_GRACE_RESOLVED,
    ROUTING_MODEL_GRACE_TIMEOUT,
    ROUTING_STARTUP_QUEUED,
    ROUTING_STARTUP_RESOLVED,
    ROUTING_STARTUP_TIMEOUT,
)

__all__ = [
    'EVICTION_COOLDOWN_APPLIED',
    'EVICTION_COOLDOWN_BLOCKED',
    'EVICTION_DEMAND_APPLIED',
    'GATEWAY_PHANTOM_MODEL_CLEANED',
    'GATEWAY_PHANTOM_MODEL_DETECTED',
    'GATEWAY_VRAM_ORPHAN_DETECTED',
    'GATEWAY_VRAM_STALENESS_DETECTED',
    'MODEL_CAPACITY_OVERFLOW_ASSIGNED',
    'MODEL_LOAD_COMPLETED',
    'MODEL_LOAD_INITIATED',
    'MODEL_LOAD_OVERFLOW_STARTED',
    'REQUEST_GATEWAY_TRACE',
    'REQUEST_ROUTED',
    'ROUTING_DECISION',
    'ROUTING_DECISION_FAILED',
    'ROUTING_DEQUEUED',
    'ROUTING_DRAIN_INITIATED',
    'ROUTING_EVICTION_EXECUTE_FAILED',
    'ROUTING_EVICTION_WAIT_CANCELLED',
    'ROUTING_EVICTION_WAIT_RESOLVED',
    'ROUTING_EVICTION_WAIT_STARTED',
    'ROUTING_EVICTION_WAIT_TIMEOUT',
    'ROUTING_INFERENCE_OOM_RECOVERY_STARTED',
    'ROUTING_INFERENCE_OOM_RECOVERY_SUCCEEDED',
    'ROUTING_MODEL_GRACE_QUEUED',
    'ROUTING_MODEL_GRACE_RESOLVED',
    'ROUTING_MODEL_GRACE_TIMEOUT',
    'ROUTING_OVERFLOW_FAILED',
    'ROUTING_OVERFLOW_TRIGGERED',
    'ROUTING_QUEUED',
    'ROUTING_STARTUP_QUEUED',
    'ROUTING_STARTUP_RESOLVED',
    'ROUTING_STARTUP_TIMEOUT',
    'ROUTING_TIMEOUT',
    'TOKEN_COUNTING_FAILED',
    'TOKEN_COUNT_COMPLETED',
    'TOKEN_COUNT_PRECONDITION',
    'EvictionCooldownApplied',
    'EvictionCooldownBlocked',
    'EvictionDemandApplied',
    'ModelCapacityOverflowAssigned',
    'ModelLoadCompleted',
    'ModelLoadInitiated',
    'ModelLoadOverflowStarted',
    'OomRecoveryStarted',
    'OomRecoverySucceeded',
    'RequestGatewayTrace',
    'RequestRouted',
    'RoutingDecision',
    'RoutingDecisionFailed',
    'RoutingDequeued',
    'RoutingDrainInitiated',
    'RoutingEvictionExecuteFailed',
    'RoutingEvictionWaitCancelled',
    'RoutingEvictionWaitResolved',
    'RoutingEvictionWaitStarted',
    'RoutingEvictionWaitTimeout',
    'RoutingModelGraceQueued',
    'RoutingModelGraceResolved',
    'RoutingModelGraceTimeout',
    'RoutingOverflowFailed',
    'RoutingOverflowTriggered',
    'RoutingQueued',
    'RoutingStartupQueued',
    'RoutingStartupResolved',
    'RoutingStartupTimeout',
    'RoutingTimeout',
    'TokenCountCompleted',
    'TokenCountingFailed',
    'TokenCountPrecondition',
]
