from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import Response
from universal_event_bus import EventBus
from universal_logging import get_logger

# Import from service root (not using ... to avoid "beyond top-level package" error)
from gateway_client import GatewayConfig
from gateways import SingleGatewayManager
from monitoring import StargateMonitor
from src.schemas.chat_completion import ChatCompletionRequest
from systems.routing.telemetry import TelemetryFreshnessWaiter

from ..authorization import AuthorizationManager
from ..parameter_management import ParameterManager
from ..stargate_config import StargateConfig
from ..token_management import TokenManager
from ..utils.request_context import ForwardContext
from .requests import process_chat_completion
from .runtime import shutdown_proxy, startup_proxy

if TYPE_CHECKING:  # pragma: no cover - type checking only imports
    from fastapi import FastAPI

    from systems.federation import FederationIntegration
    from systems.federation.master.orchestration import FederatedLoadOrchestrator

    from ...profiles import ProfileManager
    from ..core.nonstreaming import RequestExecutor, RequestForwarder, RequestPreparer
    from ..core.streaming import StreamHandler

logger = get_logger(__name__)

GATEWAY_URL = "http://localhost:9998"
PROXY_PORT = 9999


class StargateProxy:
    """
    Orchestrates request preparation, execution, and lifecycle management for Stargate.
    """

    def __init__(
        self,
        gateway_config: GatewayConfig | None = None,
        gateway_url: str | None = None,
        config_path: str = "config/stargate_config.yaml",
    ):
        """Initialize StargateProxy with single gateway configuration."""
        # Detect execution capability early
        # INVARIANT: is_execution_capable = false ⟹ mode = MASTER ∧ ¬∃ local_gateway
        self._is_execution_capable = self._detect_execution_capability(
            gateway_config, config_path
        )

        if not self._is_execution_capable:
            logger.info(
                "🚀 Router-only Master mode detected: "
                "Skipping local gateway initialization"
            )
            # Set gateway-dependent attributes to None explicitly
            self._gateway_config = None
            self.gateway_manager = None
            self.gateway_url = None
            self.resource_aware_model_manager = None
        else:
            # Existing initialization for execution-capable Stargate
            from ..utils import _normalize_gateway_config

            try:
                self._gateway_config = _normalize_gateway_config(
                    gateway_config=gateway_config,
                    gateway_url=gateway_url,
                    default_url=GATEWAY_URL,
                )
            except ValueError as exc:
                logger.critical("Gateway configuration error: %s", exc)
                raise

            # CRITICAL: Validate socket_path is preserved after normalization
            if gateway_config and isinstance(gateway_config, GatewayConfig):
                original_socket_path = gateway_config.socket_path
                if original_socket_path and not self._gateway_config.socket_path:
                    error_msg = (
                        "CRITICAL: socket_path was lost during normalization! "
                        f"Original: {original_socket_path}, "
                        f"Current: {self._gateway_config.socket_path}, "
                        f"base_url: {self._gateway_config.base_url}"
                    )
                    logger.error(error_msg)
                    raise ValueError(error_msg)

            # CRITICAL: Config suggests Unix socket but socket_path is None.
            if (
                self._gateway_config.base_url == "http://localhost"
                and not self._gateway_config.socket_path
            ):
                import os

                config_path_env = os.environ.get("STARGATE_CONFIG", config_path)
                try:
                    with open(config_path_env) as f:
                        config_content = f.read()
                        if "unix://" in config_content:
                            error_msg = (
                                "CRITICAL: Config suggests Unix socket "
                                "but socket_path is None! "
                                f"Config file: {config_path_env}, "
                                f"socket_path={self._gateway_config.socket_path}, "
                                f"base_url={self._gateway_config.base_url}"
                            )
                            logger.error(error_msg)
                            raise ValueError(error_msg)
                except (FileNotFoundError, OSError):
                    pass  # Config file not found, skip validation

            self.gateway_manager: SingleGatewayManager | None = None
            self.gateway_url = self._gateway_config.base_url

            gateway_name = self._gateway_config.name
            gateway_url = self._gateway_config.base_url
            logger.info("Initializing StargateProxy with gateway: %s", gateway_name)
            logger.info("  - %s: %s", gateway_name, gateway_url)

            # Diagnostic logging
            logger.info(
                f"StargateProxy._gateway_config: name={self._gateway_config.name}, "
                f"socket_path={self._gateway_config.socket_path}, "
                f"base_url={self._gateway_config.base_url}"
            )

        # Components that work regardless of execution capability
        self.config = StargateConfig(config_path)

        token_config = self.config.get_token_management_config()
        self.token_management_enabled = token_config.get("enabled", True)

        auth_config = self.config.get_authorization_config()
        self.authorization_manager = AuthorizationManager(auth_config)

        # Debug event broadcasting (persistence enabled by default for agent debugging)
        self._debug_broadcaster = None
        debug_config = self.config.get_debug_event_config()

        # Persistence takes priority (agent debugging)
        persistence_config = debug_config.get("persistence", {})
        pipeline_persistence_config = self.config.get_pipeline_event_config()
        socket_path = debug_config.get("socket_path")

        # Create broadcaster if socket or persistence enabled
        if (
            socket_path
            or persistence_config.get("enabled")
            or pipeline_persistence_config.get("enabled")
        ):
            from universal_event_bus import MinimalEventDebugBroadcaster

            self._debug_broadcaster = MinimalEventDebugBroadcaster(
                socket_path=socket_path,
                persistence_config=persistence_config
                if persistence_config.get("enabled")
                else None,
                pipeline_persistence_config=pipeline_persistence_config
                if pipeline_persistence_config.get("enabled")
                else None,
            )
            if socket_path:
                logger.info("Debug event socket enabled: %s", socket_path)
            if persistence_config.get("enabled"):
                logger.info(
                    "Debug event persistence enabled: %s",
                    persistence_config["directory"],
                )
            if pipeline_persistence_config.get("enabled"):
                logger.info(
                    "Pipeline event persistence enabled: %s",
                    pipeline_persistence_config["directory"],
                )

        self.event_bus = EventBus(debug_broadcaster=self._debug_broadcaster)

        # Telemetry waiter for Master mode (router-only) sticky model waiting
        # Edge mode uses gateway_manager.await_execution_completion() instead
        if not self._is_execution_capable:
            self._telemetry_waiter = TelemetryFreshnessWaiter(event_bus=self.event_bus)
        else:
            self._telemetry_waiter = None

        # Token/parameter managers only for execution-capable
        if self._is_execution_capable:
            self.token_manager = TokenManager(
                self.gateway_url,
                self.config,
                event_bus=self.event_bus,
            )
            self.parameter_manager = ParameterManager(self.gateway_url)
        else:
            self.token_manager = None
            self.parameter_manager = None

        async_monitoring_config = self.config.get_async_monitoring_config()
        self.monitor = StargateMonitor(
            enabled=async_monitoring_config.get("enabled", True),
            event_bus=self.event_bus,
            transport_config=async_monitoring_config,
        )

        self.http_client: httpx.AsyncClient | None = None

        # Public attribute - set by initialize_request_components()
        self.profile_manager: ProfileManager | None = None
        self.request_preparer: RequestPreparer | None = None
        self.request_executor: RequestExecutor | None = None
        self.request_forwarder: RequestForwarder | None = None
        self.stream_handler: StreamHandler | None = None

        self.gateway_logger = None
        self.routing_consumer = None
        self.monitoring_consumer = None
        self.metrics_consumer = None
        self.model_cache_consumer = None
        self.resource_consumer = None
        self.routing_metrics_consumer = None
        self.routing_decision_consumer = None
        self.dashboard = None
        self.websocket_manager = None

        self.shutdown_handler = None

        self.pipeline_registry = None
        self.pipeline_executor = None
        self.pipeline_hot_reload = None
        self.profile_watcher = None
        self.federation_integration: FederationIntegration | None = None

        # Federation orchestrator (set by startup, Master mode only)
        self.federated_load_orchestrator: FederatedLoadOrchestrator | None = None

    def _detect_execution_capability(
        self, gateway_config: GatewayConfig | None, config_path: str
    ) -> bool:
        """
        Detect if this Stargate is execution-capable based on config.

        INVARIANT: Returns False ⟹ mode = MASTER ∧ gateway.url = null

        Args:
            gateway_config: Gateway config passed to __init__
            config_path: Path to stargate config file

        Returns:
            True if execution-capable, False if router-only
        """
        import os

        import yaml

        # If gateway_config provided, execution capable
        if gateway_config is not None:
            return True

        # Load config file to check federation mode and gateway config
        actual_path = os.environ.get("STARGATE_CONFIG", config_path)
        try:
            with open(actual_path) as f:
                config = yaml.safe_load(f) or {}
        except (FileNotFoundError, OSError):
            return True  # Default: execution capable

        federation = config.get("federation", {})
        mode = federation.get("mode", "edge")

        # Check gateway configuration for all modes
        gateway = config.get("gateway", {})
        url = gateway.get("url")
        socket_path = gateway.get("socket_path")
        has_local_gateway = (url is not None) or (socket_path is not None)

        if mode == "master":
            # Master: router-only if no local gateway
            return has_local_gateway
        elif mode == "remote":
            # REMOTE mode = Relay Stargate
            # Relay with local_edge → router-only (forwards to Edge container)
            # Relay with gateway → execution-capable (direct execution)
            local_edge = federation.get("local_edge")
            if local_edge:
                logger.info(
                    f"Relay with local_edge (edge={local_edge.get('stargate_id')}): "
                    f"Router-only mode"
                )
                return False
            else:
                # Relay with gateway section (direct execution pattern)
                return has_local_gateway
        else:  # edge, standalone
            # Edge: passive container that proxies Gateway
            return True

    @property
    def is_execution_capable(self) -> bool:
        """Whether this Stargate can execute inference locally."""
        return self._is_execution_capable

    async def startup(self, app: FastAPI | None = None) -> None:
        """Initialize async components."""
        await startup_proxy(self, app)

    async def shutdown(self) -> None:
        """Cleanup async components."""
        await shutdown_proxy(self)

    @property
    def federated_manager(self):
        """
        Get FederatedGatewayManager if federation is enabled and in Master mode.

        Returns:
            FederatedGatewayManager | None
        """
        if self.federation_integration:
            return self.federation_integration.federated_manager
        return None

    @property
    def federation_forwarder(self):
        """
        Get FederatedRequestForwarder if federation is enabled and in Master mode.

        Returns:
            FederatedRequestForwarder | None
        """
        if self.federation_integration:
            return self.federation_integration.forwarder
        return None

    @property
    def federation_circuit_breaker(self):
        """
        Get FederationCircuitBreaker if federation is enabled and in Master mode.

        Returns:
            FederationCircuitBreaker | None
        """
        if self.federation_integration:
            return self.federation_integration.circuit_breaker
        return None

    def create_batch_router(self):
        """
        Create a BatchRouter instance (dependency injection for pipeline).

        This factory method enables pipeline domain to use batch routing
        without importing BatchRouter directly, maintaining domain isolation.

        Returns:
            BatchRouter with generic dict-based interface
        """
        from ..core.control_plane.placement.batch.router import BatchRouter

        return BatchRouter(
            gateway_manager=self.gateway_manager,
            routing_ops=self.resource_aware_model_manager._routing_ops,
        )

    async def submit_chat_request(
        self,
        request: Request,
        chat_request: ChatCompletionRequest,
        model_override: str | None = None,
        profile_override: str | None = None,
        disable_profile: bool = False,
        skip_token_counting: bool | None = None,
    ) -> Response:
        """Public API: Submit a chat completion request."""
        return await self.process_chat_completion(
            request,
            chat_request,
            model_override=model_override,
            profile_override=profile_override,
            disable_profile=disable_profile,
            skip_token_counting=skip_token_counting,
        )

    async def process_chat_completion(
        self,
        request: Request,
        chat_request: ChatCompletionRequest,
        model_override: str | None = None,
        profile_override: str | None = None,
        disable_profile: bool = False,
        skip_token_counting: bool | None = None,
    ) -> Response:
        """Internal helper for processing chat completions."""
        return await process_chat_completion(
            proxy=self,
            request=request,
            chat_request=chat_request,
            model_override=model_override,
            profile_override=profile_override,
            disable_profile=disable_profile,
            skip_token_counting=skip_token_counting,
        )

    async def forward_request(
        self,
        method: str,
        path: str,
        headers: dict[str, str],
        content: bytes | None = None,
        params: dict[str, Any] | None = None,
        context: ForwardContext | None = None,
        request: Request | None = None,
    ) -> Response:
        """Forward a non-streaming request to the gateway."""
        if self.request_forwarder is None:
            raise HTTPException(status_code=500, detail="Request forwarder unavailable")
        return await self.request_forwarder.forward_request(
            method=method,
            path=path,
            headers=headers,
            content=content,
            params=params,
            context=context,
            request=request,
        )

    async def forward_streaming_request(
        self,
        method: str,
        path: str,
        headers: dict[str, str],
        content: bytes | None = None,
        params: dict[str, Any] | None = None,
        context: ForwardContext | None = None,
        request: Request | None = None,
    ):
        """Forward a streaming request to the gateway."""
        request_id = context.request_id[:8] if context else "unknown"
        logger.info(
            "🌊 [REQ:%s] stargate_core.forward_streaming_request() called", request_id
        )

        if self.stream_handler is None:
            raise HTTPException(
                status_code=500, detail="Stream handler not initialized"
            )

        logger.info(
            "🌊 [REQ:%s] About to call stream_handler.forward_streaming_request()",
            request_id,
        )
        result = await self.stream_handler.forward_streaming_request(
            method=method,
            path=path,
            headers=headers,
            content=content,
            params=params,
            context=context,
            request=request,
        )
        logger.info("🌊 [REQ:%s] stream_handler returned: %s", request_id, type(result))
        return result

    async def process_embedding_request(
        self,
        model_id: str,
        input_texts: list[str],
        request: Request,
    ) -> dict:
        """
        Process embedding request through federation.

        Args:
            model_id: Embedding model identifier
            input_texts: Texts to embed
            request: Original FastAPI request

        Returns:
            OpenAI-compatible embedding response
        """
        request_id_val = getattr(request.state, "request_id", None)

        # Build embedding request body for forwarding
        request_body = {
            "model": model_id,
            "input": input_texts,
        }

        # Use pre-initialized RequestExecutor (from component_factory)
        result = await self.request_executor.execute_embedding_request(
            model_id=model_id,
            request_body=request_body,
            request_id=request_id_val,
        )

        return result

    def cancel_request(self, request_id: str, model_id: str | None = None) -> bool:
        """
        Cancel a pending request from all applicable queues.

        Attempts cancellation from capacity and model queues.
        Fire-and-forget: logs failures but doesn't raise.

        Args:
            request_id: The request ID to cancel (matches context.request_id)
            model_id: Optional model ID for model-specific queues.
                If None, skips model queues.

        Returns:
            True if cancelled from any queue, False if not found in any

        Note:
            Queue-based cancellation was removed with unified capacity tracking.
            Remote cancellation (in-flight) is handled via MasterRequestTracker.
        """
        logger.debug(
            "Queue cancellation disabled (no waiting queues): request=%s model=%s",
            request_id[:8],
            model_id,
        )
        return False
