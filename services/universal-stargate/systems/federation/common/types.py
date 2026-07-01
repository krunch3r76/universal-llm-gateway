"""
Federation protocol types.

Core types for federation state management and request tracking.
Wire format uses MessageEnvelope (see common/protocol/message.py).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from model_id import ModelId

# Federation HTTP headers
HEADER_FEDERATION_SOURCE = "X-Federation-Source"
HEADER_FEDERATION_KEY = "X-Federation-Key"
HEADER_FEDERATION_HOP_COUNT = "X-Federation-Hop-Count"
HEADER_REQUEST_ID = "X-Request-ID"

FEDERATION_HEADERS: frozenset[str] = frozenset(
    [
        HEADER_FEDERATION_SOURCE,
        HEADER_FEDERATION_KEY,
        HEADER_FEDERATION_HOP_COUNT,
        HEADER_REQUEST_ID,
    ]
)


class RequestState(StrEnum):
    """Request lifecycle states for tracking."""

    ACTIVE = "active"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    EXPIRED = "expired"


@dataclass(slots=True, kw_only=True)
class FederationRequestMetadata:
    """Federation metadata for forwarded requests."""

    source_stargate: str
    request_id: str
    hop_count: int
    max_hops: int
    hints: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_stargate": self.source_stargate,
            "request_id": self.request_id,
            "hop_count": self.hop_count,
            "max_hops": self.max_hops,
            **({"hints": self.hints} if self.hints else {}),
        }


@dataclass(slots=True, kw_only=True)
class TrackedRequest:
    """A tracked request for cancellation and compute-type tracking."""

    request_id: str
    remote_id: str
    remote_request_id: str
    started_at: float = field(default_factory=time.time)
    state: RequestState = RequestState.ACTIVE
    retry_count: int = 0

    # Compute-type tracking (for cleanup)
    endpoint_category: str | None = None
    compute_type: str | None = None


@dataclass(slots=True, kw_only=True)
class FederatedGateway:
    """
    Gateway instance accessible via Remote Stargate.

    This is the INSTANCE (behavior + state), not just a snapshot.
    Gets wrapped in a Gateway snapshot by collect_gateways().

    INVARIANT:
      ∀ state_field: updated only via update_from_telemetry()
      ∧ ¬manual_state_updates
    """

    # Identity
    gateway_id: str
    remote_stargate_id: str
    remote_stargate_url: str
    node_id: str = ""

    # Backend type: "federated" (default), "cloud_api", future: "vps"
    backend_type: str = "federated"
    provider_url: str = ""
    provider_api_key: str = ""
    provider_name: str = ""

    # Resource state (from RESOURCE_UPDATE)
    ram_free_mb: int = 0
    vram_free_mb: int = 0
    ram_total_mb: int = 0
    vram_total_mb: int = 0

    # Model state (from telemetry) - typed frozensets per ModelId architecture
    loaded_models: frozenset[ModelId] = field(default_factory=frozenset)
    busy_models: frozenset[ModelId] = field(default_factory=frozenset)
    loading_models: frozenset[ModelId] = field(default_factory=frozenset)
    # available_models: Measured catalog (vram/ram in ~/.gateway/catalog)
    available_models: frozenset[ModelId] = field(default_factory=frozenset)
    # activated_models: Filtered subset for public /v1/models endpoint
    # None = not provided (fallback to available_models), frozenset() = explicitly empty
    activated_models: frozenset[ModelId] | None = None

    # Model resource requirements (PHASE 2 FIX: for Master routing decisions)
    # Maps ModelId -> {vram_usage: int, ram_usage: int, input_schema: str}
    # Populated from telemetry "model_resources" field; values carry per-model
    # routing data (max_concurrent_requests, context_length) and the optional
    # cloud "dispatch" facet (mirror of libs CapabilityDispatch).
    model_resources: dict[ModelId, dict[str, Any]] = field(default_factory=dict)

    # Activation rules from Edge catalog (for Master-side filtering if needed)
    activated_contexts: dict[str, dict[str, list[int]]] = field(default_factory=dict)

    # Request state
    active_requests: int = 0

    # Telemetry freshness
    telemetry_timestamp: float = field(default_factory=time.time)
    last_heartbeat: float = field(default_factory=time.time)

    # HTTP polling flag (for Golem/disable_websocket remotes)
    is_http_polling: bool = False

    # When False, gateway contributes catalog visibility but is excluded from
    # chat/completions candidate selection (cursor-sdk catalog poller).
    dispatchable: bool = True

    # Eviction hysteresis: monotonic timestamp when each model was loaded.
    # Populated by gateway manager from MODEL_LOADED telemetry.
    model_loaded_at: dict[ModelId, float] = field(default_factory=dict)

    # Delta tracking (for HTTP polling)
    _last_sequence_number: int = field(default=0, init=False)

    @property
    def is_cloud(self) -> bool:
        """True if this gateway proxies to a cloud API provider."""
        return self.backend_type == "cloud_api"

    @property
    def telemetry_age_ms(self) -> int:
        """Age of last RESOURCE_UPDATE in milliseconds (for observability/scoring)."""
        if self.is_cloud:
            return 0
        return int((time.time() - self.telemetry_timestamp) * 1000)

    @property
    def heartbeat_age_ms(self) -> int:
        """Age of last heartbeat/signal in milliseconds."""
        if self.is_cloud:
            return 0
        return int((time.time() - self.last_heartbeat) * 1000)

    @property
    def is_unreachable(self) -> bool:
        """Check if gateway is unreachable (> 60s no signal of any kind)."""
        if self.is_cloud:
            return False
        return self.heartbeat_age_ms > 60000


# Protocol version
PROTOCOL_VERSION = "1.0"

# WebSocket close codes for federation
WS_CLOSE_PROTOCOL_MISMATCH = 4002
WS_CLOSE_AUTH_DEADLINE = 4003
WS_CLOSE_IDENTITY_COLLISION = 4004
WS_CLOSE_AUTH_FAILED = 4005


def validate_version(remote_version: str | None) -> bool:
    """Validate protocol version compatibility."""
    if not remote_version:
        return False
    # Phase 1: Strict equality
    return remote_version == PROTOCOL_VERSION


# --- Telemetry Parsing Utilities (moved from ws_client/telemetry_receiver.py) ---


def parse_telemetry_payload(_msg_type: str, data: dict[str, Any]) -> dict[str, Any]:
    """
    Parse telemetry data, converting model IDs to ModelId objects.

    Args:
        _msg_type: Message type (reserved for future type-specific parsing)
        data: Raw telemetry data dictionary

    Returns:
        Parsed data with ModelId objects and frozensets

    INVARIANT:
      ∀ model_id field: converted to ModelId at this boundary
      ∀ model list field: converted to frozenset[ModelId]
    """
    from universal_logging import get_logger

    logger = get_logger(__name__)
    parsed = data.copy()

    # Single model_id field
    if "model_id" in parsed:
        try:
            parsed["model_id"] = ModelId.parse(parsed["model_id"])
        except (TypeError, ValueError) as e:
            logger.warning(f"Failed to parse model_id: {e}")

    # List fields -> frozenset[ModelId] (per-item tolerant: drop bad, keep good)
    list_fields = (
        "loaded_models",
        "busy_models",
        "loading_models",
        "available_models",
        "activated_models",
    )
    for field_name in list_fields:
        if field_name in parsed and isinstance(parsed[field_name], list):
            valid: list[ModelId] = []
            dropped = 0
            for raw in parsed[field_name]:
                try:
                    valid.append(ModelId.parse(raw))
                except (TypeError, ValueError):
                    dropped += 1
            if dropped:
                logger.warning(
                    "Dropped %d/%d malformed entries in %s",
                    dropped,
                    dropped + len(valid),
                    field_name,
                )
            parsed[field_name] = frozenset(valid)

    # PHASE 2 FIX: Parse model_resources dict (ModelId keys)
    # model_resources: {model_id_str: {vram_usage: int, ram_usage: int}}
    if "model_resources" in parsed and isinstance(parsed["model_resources"], dict):
        try:
            parsed["model_resources"] = {
                ModelId.parse(model_id): resource_dict
                for model_id, resource_dict in parsed["model_resources"].items()
            }
        except (TypeError, ValueError) as e:
            logger.warning(f"Failed to parse model_resources: {e}")
            parsed["model_resources"] = {}

    # Parse model_vram dict (ModelId keys) — measured VRAM for eviction planning
    if "model_vram" in parsed and isinstance(parsed["model_vram"], dict):
        try:
            parsed["model_vram"] = {
                ModelId.parse(model_id): vram_mb
                for model_id, vram_mb in parsed["model_vram"].items()
            }
        except (TypeError, ValueError) as e:
            logger.warning(f"Failed to parse model_vram: {e}")
            parsed["model_vram"] = {}

    return parsed


def extract_resource_state(parsed_payload: dict[str, Any]) -> dict[str, Any]:
    """
    Extract resource state from RESOURCE_UPDATE payload.

    INVARIANT: resource_update is complete snapshot (authoritative)

    Returns:
        Resource state including model_resources for routing
    """
    return {
        "ram_free_mb": parsed_payload.get("available_ram_mb", 0),
        "vram_free_mb": parsed_payload.get("available_vram_mb", 0),
        "ram_total_mb": parsed_payload.get("total_ram_mb", 0),
        "vram_total_mb": parsed_payload.get("total_vram_mb", 0),
        "loaded_models": parsed_payload.get("loaded_models", frozenset()),
        "busy_models": parsed_payload.get("busy_models", frozenset()),
        "available_models": parsed_payload.get("available_models", frozenset()),
        # None = not provided, frozenset() = explicitly empty
        "activated_models": parsed_payload.get("activated_models"),
        "active_requests": parsed_payload.get("active_requests", 0),
        # PHASE 2 FIX: Include model resource requirements for routing
        "model_resources": parsed_payload.get("model_resources", {}),
        "activated_contexts": parsed_payload.get("activated_contexts", {}),
    }
