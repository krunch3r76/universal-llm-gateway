"""
Request context container for non-streaming request processing.
"""

from typing import TYPE_CHECKING, Any

from model_id import ModelId

if TYPE_CHECKING:
    from fastapi import Request

    from gateway_client import ModelMetadata
    from gateways import GatewayInstance
    from src.schemas.chat_completion import ChatCompletionRequest
    from src.schemas.tokens import TokenMetrics
    from systems.federation.common.config.schema import EndpointCategory
    from systems.routing.selection.types import Gateway

    from ....profiles import ProfileData


class RequestContext:
    """Container for fully prepared request context."""

    def __init__(
        self,
        request_id: str,
        start_time: float,
        selected_model: ModelId,
        original_request: dict[str, Any],
        raw_client_fields: dict[str, Any],
        user_params: dict[str, Any],
        middleware_actions: list[str],
        bypass_transformations: bool = False,
        disable_profile: bool = False,
        skip_token_counting: bool = False,
        http_request: "Request | None" = None,
        chat_request: "ChatCompletionRequest | None" = None,
        selected_gateway: "Gateway | None" = None,
    ):
        self.request_id = request_id
        self.start_time = start_time
        self.selected_model = selected_model
        self.original_request = original_request
        self.raw_client_fields = raw_client_fields
        self.user_params = user_params
        self.middleware_actions = middleware_actions
        self.bypass_transformations = bypass_transformations
        self.disable_profile = disable_profile
        self.skip_token_counting = skip_token_counting
        self.http_request = http_request
        self.chat_request = chat_request
        self.selected_gateway = selected_gateway

        # Will be populated during processing
        self.processed_messages: list[dict[str, Any]] | None = None
        self.transformation_metadata: dict[str, Any] = {}
        self.token_metrics: TokenMetrics | None = None
        self.modified_request: dict[str, Any] | None = None
        # Canonical ModelMetadata object
        self.model_metadata: ModelMetadata | None = None
        self.client_wants_streaming: bool = False
        # One-shot per-request profile name (if any)
        self.request_profile: str | None = None
        self.selected_gateway_instance: GatewayInstance | None = None
        # Profile data (resolved once, used throughout request processing)
        self.profile_data: ProfileData | None = None
        # Routing policy:
        # - sticky=True: ∀ model_id, ∃! gateway where model is loaded
        # - sticky=False: model may be loaded on multiple gateways
        self.model_sticky: bool = True

        # Pipeline execution context (optional, set by header detection)
        self.pipeline_execution_id: str | None = None
        self.pipeline_step_id: str | None = None

        # Federation timeout hint (passed from pipeline step timeout)
        self.request_timeout_hint: float | None = None

        # Cancel group ID (X-Pipeline-Cancel-Group header), for group cancellation
        self.cancel_group: str | None = None

        # Endpoint category determined during routing (for consistent capacity tracking)
        self.routing_endpoint_category: EndpointCategory | None = None

        # Gateways to exclude from routing (accumulated across retries)
        self.excluded_gateway_ids: set[str] = set()

        # Per-gateway upstream error context (gateway_id → {status_code, message})
        # Populated alongside excluded_gateway_ids so the final error can
        # surface the original upstream status (e.g. 429) to the client.
        self.excluded_gateway_errors: dict[str, dict[str, Any]] = {}

        # Capacity token acquired during routing, released after execution
        self.capacity_token: Any | None = None

        # Monotonic deadline for capacity waiting (set by retry loop).
        # Inner mechanisms (admission, pre-route queue, eviction wait) use
        # this to compute their remaining timeout instead of static defaults.
        self._capacity_deadline_mono: float | None = None

    @property
    def is_federated(self) -> bool:
        """
        True if request should be forwarded to a federated gateway.

        Post-unification: Always True when gateway selected (all gateways federated).
        """
        return self.selected_gateway is not None

    @property
    def federated_gateway(self):
        """
        Get FederatedGateway if federated, else None.

        Post-unification: All gateways are federated, so this returns
        selected_gateway.ref when gateway is selected.
        """
        if self.selected_gateway is None:
            return None
        # All gateways are FederatedGateway post-unification
        return self.selected_gateway.ref

    @property
    def target_gateway_id(self) -> str | None:
        """
        Get target gateway ID for slot tracking.

        Returns:
            Gateway ID where request is routed:
            - Federated: selected_gateway.name
            - Local: selected_gateway_instance.config.name
            - None: No gateway selected yet
        """
        if self.selected_gateway:
            return self.selected_gateway.name
        if self.selected_gateway_instance:
            return self.selected_gateway_instance.config.name
        return None
