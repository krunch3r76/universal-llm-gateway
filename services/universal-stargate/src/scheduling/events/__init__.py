"""Event signals for Universal Stargate event-driven architecture.

All events use the UML Message structure from universal_event_bus.
Events are created via factory functions and published as Event objects.

The EventBus automatically injects:
- timestamp: ISO 8601 string with milliseconds and Z suffix
- id: Global counter for event ordering

Usage:
    from src.scheduling.events import GatewayStateChanged
    event = GatewayStateChanged(
        url="http://localhost:9998",
        connectivity="reachable",
        health="healthy",
        previous_connectivity=None,
        previous_health=None,
        transition_type="initial",
    )
    await event_bus.publish_nowait(event)

Domain modules:
    routing             — routing metrics, model load, token count, decision
    routing_failures    — infeasibility, eviction, upstream exclusion, divergence
    gateway             — state transitions, retries, resource updates, reservations
    model_lifecycle     — loaded/unloaded, loading, execution, capacity
    request             — request lifecycle, profile, federation snapshot
    queue               — master capacity queue
    federation_signaling — connection, telemetry, routing delegation
    federation_load     — catalog changes, VRAM drift, load orchestration
    cloud               — cloud proxy availability and catalog
    proxy               — federated request proxy layer: transformation decisions
    pipeline            — registry, embedding steps, domain verification
    system              — startup and shutdown"""

# ruff: noqa: F403, F405

from .cloud import *  # noqa: F403
from .cursor_catalog import *  # noqa: F403
from .federation_load import *  # noqa: F403
from .federation_signaling import *  # noqa: F403
from .gateway import *  # noqa: F403
from .model_lifecycle import *  # noqa: F403
from .pipeline import *  # noqa: F403
from .proxy import *  # noqa: F403
from .queue import *  # noqa: F403
from .request import *  # noqa: F403
from .routing import *  # noqa: F403
from .routing_debug import *  # noqa: F403
from .routing_failures import *  # noqa: F403
from .snapshot import *  # noqa: F403
from .system import *  # noqa: F403

__all__ = [
    # ── routing ──────────────────────────────────────────────────────────────
    "REQUEST_ROUTED",
    "MODEL_LOAD_INITIATED",
    "MODEL_LOAD_COMPLETED",
    "MODEL_CAPACITY_OVERFLOW_ASSIGNED",
    "MODEL_LOAD_OVERFLOW_STARTED",
    "REQUEST_GATEWAY_TRACE",
    "TOKEN_COUNT_COMPLETED",
    "TOKEN_COUNT_PRECONDITION",
    "TOKEN_COUNTING_FAILED",
    "ROUTING_DECISION",
    "ROUTING_DECISION_FAILED",
    "RequestRouted",
    "RequestGatewayTrace",
    "ModelLoadInitiated",
    "ModelLoadCompleted",
    "ModelCapacityOverflowAssigned",
    "ModelLoadOverflowStarted",
    "TokenCountCompleted",
    "TokenCountPrecondition",
    "TokenCountingFailed",
    "RoutingDecision",
    "RoutingDecisionFailed",
    # ── routing_debug ───────────────────────────────────────────────────────
    "ROUTING_DEBUG_GATEWAY_DROPOUT",
    "ROUTING_DEBUG_GATEWAY_REMOVED",
    "ROUTING_DEBUG_GATEWAY_REGISTERED",
    "RoutingDebugGatewayDropout",
    "RoutingDebugGatewayRemoved",
    "RoutingDebugGatewayRegistered",
    # ── routing_failures ─────────────────────────────────────────────────────
    "ROUTING_RESOURCE_DATA_MISSING",
    "ROUTING_MODEL_INFEASIBLE",
    "ROUTING_EVICTION_BLOCKED_BUSY",
    "ROUTING_EVICTION_INSUFFICIENT_PERMANENT",
    "ROUTING_UPSTREAM_ALL_EXCLUDED",
    "ROUTING_CAPACITY_DIVERGENCE",
    "ROUTING_CAPACITY_PRESEEDED",
    "ROUTING_OVERFLOW_TRIGGERED",
    "ROUTING_OVERFLOW_FAILED",
    "CAPACITY_SLOT_LEAK_RECOVERED",
    "RoutingResourceDataMissing",
    "RoutingModelInfeasible",
    "RoutingEvictionBlockedBusy",
    "RoutingEvictionInsufficientPermanent",
    "RoutingUpstreamAllExcluded",
    "RoutingCapacityDivergence",
    "RoutingCapacityPreseeded",
    "RoutingOverflowTriggered",
    "RoutingOverflowFailed",
    "CapacitySlotLeakRecovered",
    # ── gateway ──────────────────────────────────────────────────────────────
    "GATEWAY_STATE_CHANGED",
    "GATEWAY_RETRY_ATTEMPTED",
    "GATEWAY_RESOURCE_UPDATE",
    "RESOURCE_RESERVED",
    "RESOURCE_RELEASED",
    "GatewayStateChanged",
    "GatewayRetryAttempted",
    "GatewayResourceUpdate",
    "ResourceReserved",
    "ResourceReleased",
    # ── model_lifecycle ──────────────────────────────────────────────────────
    "MODEL_AVAILABLE",
    "MODEL_LOADED",
    "MODEL_UNLOADED",
    "MODEL_UNAVAILABLE",
    "MODEL_LOADING_STARTED",
    "MODEL_LOADING_PROGRESS",
    "MODEL_LOAD_FAILED",
    "MODEL_LOADING_STUCK",
    "MODEL_EXECUTION_STARTED",
    "MODEL_EXECUTION_COMPLETED",
    "MODEL_EXECUTION_FAILED",
    "MODEL_CAPACITY_FREED",
    "ModelAvailable",
    "ModelLoaded",
    "ModelUnloaded",
    "ModelUnavailable",
    "ModelLoadingStarted",
    "ModelLoadingProgress",
    "ModelLoadingFailed",
    "ModelLoadingStuck",
    "ModelExecutionStarted",
    "ModelExecutionCompleted",
    "ModelExecutionFailed",
    "ModelCapacityFreed",
    # ── request ──────────────────────────────────────────────────────────────
    "REQUEST_QUEUED",
    "REQUEST_PROCESSING",
    "REQUEST_INFERENCE_STARTED",
    "REQUEST_PROFILE_RESOLVED",
    "REQUEST_ALIAS_RESOLVED",
    "REQUEST_CLIENT_DISCONNECTED",
    "REQUEST_COMPLETED",
    "REQUEST_FAILED",
    "REQUEST_TIMEOUT",
    "REQUEST_REMOVED",
    "FEDERATION_SNAPSHOT_SENT",
    "RequestQueued",
    "RequestProcessing",
    "RequestInferenceStarted",
    "RequestProfileResolved",
    "RequestAliasResolved",
    "RequestClientDisconnected",
    "RequestCompleted",
    "RequestFailed",
    "RequestTimeout",
    "RequestRemoved",
    "FederationSnapshotSent",
    # ── model selection reputation ──────────────────────────────────────────
    "MODEL_SELECTION_HEALTH_OBSERVATION",
    "MODEL_SELECTION_SCORE_UPDATED",
    "MODEL_SELECTION_RANK_COMPUTED",
    "MODEL_SELECTION_SWITCH_SUPPRESSED",
    "MODEL_SELECTION_SWITCH_ALLOWED",
    "ModelSelectionHealthObservation",
    "ModelSelectionScoreUpdated",
    "ModelSelectionRankComputed",
    "ModelSelectionSwitchSuppressed",
    "ModelSelectionSwitchAllowed",
    # ── queue ────────────────────────────────────────────────────────────────
    "QUEUE_MASTER_ENTERED",
    "QUEUE_MASTER_WOKEN",
    "QUEUE_MASTER_TIMEOUT",
    "QUEUE_MASTER_TOCTOU",
    "QueueMasterEntered",
    "QueueMasterWoken",
    "QueueMasterTimedOut",
    "QueueMasterToctou",
    # ── capacity pool ───────────────────────────────────────────────────────
    "CAPACITY_POOL_QUEUED",
    "CAPACITY_POOL_WAITING",
    "CAPACITY_POOL_ADMITTED",
    "CAPACITY_POOL_FULL",
    "CAPACITY_POOL_CANCELLED",
    "CAPACITY_ADMISSION_PAUSED",
    "CAPACITY_ADMISSION_RESUMED",
    "CapacityPoolQueued",
    "CapacityPoolWaiting",
    "CapacityPoolAdmitted",
    "CapacityPoolFull",
    "CapacityPoolCancelled",
    "CapacityAdmissionPaused",
    "CapacityAdmissionResumed",
    # ── federation_signaling ─────────────────────────────────────────────────
    "FEDERATION_CONNECTION_ESTABLISHED",
    "FEDERATION_CONNECTION_LOST",
    "FEDERATION_LINK_TIMEOUT",
    "FEDERATION_CONNECTION_AUTHENTICATED",
    "FEDERATION_TELEMETRY_RECEIVED",
    "FEDERATION_TELEMETRY_MARKED_STALE",
    "FEDERATION_TELEMETRY_APPLIED",
    "FEDERATION_TELEMETRY_WIRED",
    "FEDERATION_ROUTING_DELEGATED",
    "FEDERATION_ROUTING_ROUTED_LOCAL",
    "FEDERATION_ROUTING_REJECTED",
    "FederationConnectionEstablished",
    "FederationConnectionLost",
    "FederationLinkTimeout",
    "FederationConnectionAuthenticated",
    "FederationTelemetryReceived",
    "FederationTelemetryMarkedStale",
    "FederationTelemetryApplied",
    "FederationTelemetryWired",
    "FederationRoutingDelegated",
    "FederationRoutingRoutedLocal",
    "FederationRoutingRejected",
    "FEDERATION_ACTIVATION_FILTERED_EMPTY",
    "FederationActivationFilteredEmpty",
    # ── federation_load ──────────────────────────────────────────────────────
    "FEDERATION_GATEWAY_CATALOG_CHANGED",
    "FEDERATION_CATALOG_VRAM_DRIFT",
    "FEDERATION_LOAD_REQUESTED",
    "FEDERATION_LOAD_CONFIRMED",
    "FEDERATION_LOAD_FAILED",
    "FEDERATION_ORCHESTRATOR_DECIDED",
    "FEDERATION_ORCHESTRATOR_EVICTED",
    "FederationGatewayCatalogChanged",
    "FederationCatalogVramDrift",
    "FederationLoadRequested",
    "FederationLoadConfirmed",
    "FederationLoadFailed",
    "FederationOrchestratorDecided",
    "FederationOrchestratorEvicted",
    "FederationGatewayResourceUpdateSignal",
    "FederationModelLoaded",
    "FederationModelUnloaded",
    # ── cloud ────────────────────────────────────────────────────────────────
    "CLOUD_PROXY_AVAILABLE",
    "CLOUD_PROXY_UNAVAILABLE",
    "CLOUD_PROXY_CATALOG_UPDATED",
    "CLOUD_PROXY_CATALOG_FETCH_FAILED",
    "CloudProxyAvailable",
    "CloudProxyUnavailable",
    "CloudProxyCatalogUpdated",
    "CloudProxyCatalogFetchFailed",
    # ── cursor catalog ───────────────────────────────────────────────────────
    "CURSOR_CATALOG_AVAILABLE",
    "CURSOR_CATALOG_UNAVAILABLE",
    "CURSOR_CATALOG_UPDATED",
    "CURSOR_CATALOG_FETCH_FAILED",
    "CURSOR_CATALOG_DRIFT_DETECTED",
    "CursorCatalogAvailable",
    "CursorCatalogUnavailable",
    "CursorCatalogUpdated",
    "CursorCatalogFetchFailed",
    "CursorCatalogDriftDetected",
    # ── proxy ────────────────────────────────────────────────────────────────
    "FEDERATED_REQUEST_PROMPT_TRANSFORMATION_APPLIED",
    "FEDERATED_REQUEST_PROMPT_TRANSFORMATION_FAILED",
    "FEDERATED_REQUEST_PROMPT_TRANSFORMATION_SKIPPED",
    "federated_request_prompt_transformation_applied",
    "federated_request_prompt_transformation_failed",
    "federated_request_prompt_transformation_skipped",
    # ── pipeline ─────────────────────────────────────────────────────────────
    "PIPELINE_REGISTRY_UNAVAILABLE",
    "PIPELINE_EXECUTION_TIMED_OUT",
    "PIPELINE_DEADLOCK_DETECTED",
    "PIPELINE_EXECUTION_CANCELLED",
    "PIPELINE_STEP_MODEL_DEFERRED",
    "PIPELINE_MODEL_GATE_CLAIMED",
    "PIPELINE_MODEL_GATE_RELEASED",
    "PIPELINE_MODEL_GATE_RELEASED_ON_FAILURE",
    "PIPELINE_MODEL_REGISTRY_LOOKUP_FAILED",
    "PIPELINE_DAG_EXECUTION_COMPLETED",
    "PIPELINE_STEP_EMBEDDING_STARTED",
    "PIPELINE_STEP_EMBEDDING_COMPLETED",
    "PIPELINE_STEP_EMBEDDING_FAILED",
    "PIPELINE_STEP_DOMAIN_VERIFICATION_STARTED",
    "PIPELINE_STEP_DOMAIN_VERIFICATION_COMPLETED",
    "PipelineExecutionTimedOut",
    "PipelineDeadlockDetected",
    "PipelineExecutionCancelled",
    "PipelineStepModelDeferred",
    "PipelineModelGateClaimed",
    "PipelineModelGateReleased",
    "PipelineModelGateReleasedOnFailure",
    "PipelineModelRegistryLookupFailed",
    "PipelineDagExecutionCompleted",
    "PipelineStepEmbeddingStarted",
    "PipelineStepEmbeddingCompleted",
    "PipelineStepEmbeddingFailed",
    "pipeline_registry_unavailable",
    "pipeline_step_domain_verification_started",
    "pipeline_step_domain_verification_completed",
    # ── snapshot ─────────────────────────────────────────────────────────────
    "REQUEST_SNAPSHOT_RECEIVED",
    "REQUEST_SNAPSHOT_ROUTED",
    "REQUEST_SNAPSHOT_COMPLETED",
    "REQUEST_SNAPSHOT_FAILED",
    "RequestSnapshotReceived",
    "RequestSnapshotRouted",
    "RequestSnapshotCompleted",
    "RequestSnapshotFailed",
    # ── system ───────────────────────────────────────────────────────────────
    "SYSTEM_STARTED",
    "SYSTEM_SHUTDOWN",
    "SystemStarted",
    "SystemShutdown",
]
