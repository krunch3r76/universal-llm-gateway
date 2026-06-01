"""Main GatewayClient orchestrator.

Hybrid WebSocket/HTTP client for middleware to communicate with gateway.
- WebSocket: Control plane (models, resources, catalog, health)
- HTTP: Inference requests (chat completions, token counting)
"""

from collections.abc import Callable
from typing import Any

import httpx
from universal_logging import get_logger

from gateway_websocket import GatewayWebSocketClient, ResourcesData

from .cache import ModelCache, should_apply_middleware_for_metadata
from .config import GatewayConfig, ModelMetadata
from .http_methods import HTTPMethods
from .model_management import ModelManagement

logger = get_logger(__name__)


class GatewayClient:
    """
    Hybrid WebSocket/HTTP client for communicating with gateway.

    - WebSocket: Control plane (models, resources, catalog, health)
    - HTTP: Inference requests (chat completions, token counting)

    WebSocket provides instant access to cached state with real-time updates.
    HTTP is used only for inference requests that require request/response.
    """

    def __init__(
        self,
        config: GatewayConfig,
        event_bus=None,
        on_after_init: Callable[[str, dict[str, Any]], None] | None = None,
    ):
        """Initialize gateway client with optional event bus for telemetry."""
        self.config = config
        self.base_url = config.base_url.rstrip("/")
        self._client: httpx.AsyncClient | None = None
        self._client_initialized_logged = False
        self._event_bus = event_bus

        # Diagnostic logging
        logger.info(
            f"GatewayClient.__init__(): name={config.name}, "
            f"socket_path={config.socket_path}, base_url={config.base_url}"
        )

        # Model metadata cache
        self._cache = ModelCache(ttl=300.0)

        # WebSocket client for control plane (primary connection)
        self._ws_client = GatewayWebSocketClient(
            gateway_url=config.base_url,
            gateway_name=config.name,
            reconnect_interval=5.0,
            connect_timeout=config.connectivity_timeout or 10.0,
            event_bus=event_bus,
            socket_path=config.socket_path,
            on_after_init=on_after_init,
        )

        # Delegated modules (initialized lazily after connect)
        self._http: HTTPMethods | None = None
        self._models: ModelManagement | None = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._client:
            self._client.close()

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.disconnect()

    async def connect(self) -> bool:
        """Connect to Gateway (WebSocket + HTTP).

        WebSocket connection is primary - used for control plane.
        HTTP client is secondary - used for inference requests only.

        Returns:
            True if connection successful.

        Raises:
            ConnectionError: If WebSocket connection fails.
        """
        # Diagnostic logging before connection attempt
        ws_socket = (
            self._ws_client._socket_path
            if hasattr(self._ws_client, "_socket_path")
            else "N/A"
        )
        logger.info(
            f"GatewayClient.connect(): base_url={self.base_url}, "
            f"socket_path={self.config.socket_path}, "
            f"ws_client.socket_path={ws_socket}"
        )

        # CRITICAL: Fail fast if socket_path mismatch
        if self.config.socket_path and not self._ws_client._socket_path:
            error_msg = (
                f"CRITICAL: Configuration mismatch! "
                f"config.socket_path={self.config.socket_path} but "
                f"ws_client._socket_path is None. This indicates socket_path was not "
                f"passed to GatewayWebSocketClient. Cannot proceed with TCP fallback."
            )
            logger.error(error_msg)
            raise ValueError(error_msg)

        ws_connected = await self._ws_client.connect()

        if not ws_connected:
            # Enhanced error message with full diagnostic info
            ws_socket = (
                self._ws_client._socket_path
                if hasattr(self._ws_client, "_socket_path")
                else "N/A"
            )
            error_msg = (
                f"Failed to establish WebSocket connection to {self.base_url} "
                f"(config.socket_path={self.config.socket_path}, "
                f"ws_client.socket_path={ws_socket}, "
                f"config.base_url={self.config.base_url})"
            )
            logger.error(error_msg)
            raise ConnectionError(f"Failed to connect to gateway at {self.base_url}")

        if self._client is None:
            headers = self.config.headers or {}

            connect_timeout = (
                self.config.connectivity_timeout
                if self.config.connectivity_timeout is not None
                else self.config.timeout
            )

            timeout_config = httpx.Timeout(
                connect=connect_timeout,
                read=self.config.timeout,
                write=self.config.timeout,
                pool=5.0,
            )

            limits = httpx.Limits(
                max_keepalive_connections=5,
                max_connections=10,
                keepalive_expiry=30.0,
            )

            # Configure transport based on socket_path
            if self.config.socket_path:
                # Unix socket transport
                transport = httpx.AsyncHTTPTransport(uds=self.config.socket_path)
                self._client = httpx.AsyncClient(
                    transport=transport,
                    base_url="http://localhost",  # Required but ignored
                    timeout=timeout_config,
                    headers=headers,
                    limits=limits,
                    http2=False,
                )
                if not self._client_initialized_logged:
                    logger.debug(
                        f"HTTP client initialized for gateway via Unix socket: "
                        f"{self.config.socket_path} "
                        f"(connect={connect_timeout}s, "
                        f"read/write={self.config.timeout}s)"
                    )
                    self._client_initialized_logged = True
            else:
                # TCP transport (legacy)
                self._client = httpx.AsyncClient(
                    base_url=self.base_url,
                    timeout=timeout_config,
                    headers=headers,
                    limits=limits,
                    http2=False,
                )
                if not self._client_initialized_logged:
                    logger.debug(
                        f"HTTP client initialized for gateway at {self.base_url} "
                        f"(connect={connect_timeout}s, "
                        f"read/write={self.config.timeout}s)"
                    )
                    self._client_initialized_logged = True

            # Initialize delegated modules
            self._http = HTTPMethods(
                self._client, self.base_url, self.config, self._event_bus
            )
            self._models = ModelManagement(self._http)

        return True

    async def disconnect(self):
        """Close WebSocket and HTTP connections."""
        await self._ws_client.disconnect()

        if self._client:
            await self._client.aclose()
            self._client = None

    # =========================================================================
    # WebSocket State Access (Instant, No HTTP)
    # =========================================================================

    @property
    def ws_client(self) -> GatewayWebSocketClient:
        """Get the WebSocket client for direct access."""
        return self._ws_client

    def is_connected(self) -> bool:
        """Check if client is connected (WebSocket must be connected)."""
        return self._ws_client.is_connected

    async def health_check(self) -> bool:
        """Check if the gateway is healthy (WebSocket connected)."""
        return self._ws_client.is_connected

    async def get_health(self) -> dict[str, Any] | None:
        """
        Fetch /health endpoint response from Gateway.

        Used for connection validation (fingerprint check).

        Returns:
            Health response dict with 'service' and 'role' fields, or None on error
        """
        if self._client is None:
            return None

        try:
            response = await self._client.get("/health")
            if response.status_code == 200:
                return response.json()
            logger.warning(f"Gateway /health returned {response.status_code}")
            return None
        except Exception as e:
            logger.debug(f"Failed to fetch gateway health: {e}")
            return None

    def get_models(self) -> set[str]:
        """Get available model IDs (instant, from WebSocket cache)."""
        return self._ws_client.get_models()

    def get_loaded_models(self) -> frozenset[str]:
        """Get currently loaded model IDs (instant, from WebSocket cache)."""
        return self._ws_client.get_loaded_models()

    def get_ws_resources(self) -> ResourcesData:
        """Get resource status (instant, from WebSocket cache)."""
        return self._ws_client.get_resources()

    def get_ws_catalog(self) -> dict[str, Any]:
        """Get catalog data (instant, from WebSocket cache)."""
        return self._ws_client.get_catalog()

    def get_activated_contexts(self) -> dict[str, dict]:
        """Get activated contexts from catalog (instant, from WebSocket cache)."""
        return self._ws_client.get_activated_contexts()

    def get_transformations(self) -> dict[str, Any]:
        """Get catalog transformations (instant, from WebSocket cache)."""
        return self._ws_client.get_transformations()

    def get_resource_status(self) -> ResourcesData | None:
        """Get current resource status from real-time WebSocket state.

        Returns:
            ResourcesData if connected and data available, None otherwise.

        Event-driven: automatically updated on RESOURCE_UPDATE events.
        No HTTP round-trip required - WebSocket maintains real-time state.
        """
        return self._ws_client.get_resource_status()

    def get_busy_models(self) -> frozenset[str]:
        """Get current set of busy models from real-time WebSocket state."""
        return self._ws_client.get_busy_models()

    def get_loading_models(self) -> frozenset[str]:
        """Get models currently loading from real-time WebSocket state."""
        return self._ws_client.get_loading_models()

    def get_gateway_url(self) -> str:
        """Get the gateway base URL."""
        return self.base_url

    def get_http_client(self) -> httpx.AsyncClient:
        """Get the underlying HTTP client for direct API calls."""
        if self._client is None:
            raise RuntimeError("GatewayClient not connected. Call connect() first.")
        return self._client

    # =========================================================================
    # HTTP Methods (Delegated)
    # =========================================================================

    async def _request(self, method: str, path: str, **kwargs) -> dict[str, Any]:
        """Make HTTP request with retry logic."""
        if self._http is None:
            await self.connect()
        return await self._http.request(method, path, **kwargs)

    async def fetch_model_info_dict(
        self, model_id: str, include_all_fields: bool = False
    ) -> dict[str, Any] | None:
        """Fetch raw model info dict (OpenAI-ish model object) from Gateway.

        Returns arbitrary fields from Gateway's model catalog. Use this for
        features requiring fields not in ModelMetadata (e.g., personality config).

        For typed resource requirements, use fetch_model_configuration().
        """
        if self._http is None:
            await self.connect()
        return await self._http.fetch_model_info_dict(model_id, include_all_fields)

    async def fetch_model_configuration(self, model_id: str) -> ModelMetadata | None:
        """Fetch typed model configuration (ModelMetadata) from Gateway.

        Returns ModelMetadata with typed fields (vram_usage, ram_usage, etc).
        Use this for resource requirements and routing decisions.

        For arbitrary fields, use fetch_model_info_dict().
        """
        if self._http is None:
            await self.connect()
        return await self._http.fetch_model_configuration(model_id)

    async def get_all_model_configurations(self) -> dict[str, ModelMetadata]:
        """Get configurations for all models at once."""
        if self._http is None:
            await self.connect()
        return await self._http.get_all_model_configurations()

    async def get_model_chat_template(self, model_id: str) -> dict[str, Any]:
        """Get chat template information for a model."""
        if self._http is None:
            await self.connect()
        return await self._http.get_model_chat_template(model_id)

    async def get_model_parameter_defaults(self, model_id: str) -> dict[str, Any]:
        """Get parameter defaults for a model."""
        if self._http is None:
            await self.connect()
        return await self._http.get_model_parameter_defaults(model_id)

    async def get_supported_parameters(self, model_id: str) -> list[str]:
        """Get supported parameters for a model."""
        if self._http is None:
            await self.connect()
        return await self._http.get_supported_parameters(model_id)

    async def get_catalog(self, include_models: bool = False) -> dict[str, Any]:
        """Get the full model catalog from Gateway."""
        if self._http is None:
            await self.connect()
        return await self._http.get_catalog(include_models)

    # =========================================================================
    # Model Management (Commands via HTTP)
    # =========================================================================

    async def load_model(self, model_id: str) -> bool:
        """Load a specific model on this gateway."""
        if self._models is None:
            await self.connect()
        return await self._models.load_model(model_id)

    async def unload_model(self, model_id: str, force: bool = False) -> bool:
        """Unload a model from the gateway.

        Args:
            model_id: Model to unload
            force: If True, force-kill process immediately

        Returns:
            True if unload initiated (caller should wait for MODEL_UNLOADED event)
        """
        if self._models is None:
            await self.connect()

        # CRITICAL: Remove from _loaded_models immediately to prevent stale routing
        # decisions during the 5+ second window between unload initiation and
        # MODEL_UNLOADED event arrival. Without this, concurrent requests see
        # model as loaded (T1_FEASIBLE_NOW) and fail with 503 during eviction.
        # The MODEL_UNLOADED event handler will redundantly call discard() (idempotent).
        self._ws_client._state._loaded_models.discard(model_id)

        try:
            result = await self._models.unload_model(model_id, force=force)
            if not result:
                # Unload request failed (model busy, not found, or skipped)
                # Rollback state since model is likely still loaded
                self._ws_client._state._loaded_models.add(model_id)
            return result
        except Exception:
            # Exception during unload - rollback
            self._ws_client._state._loaded_models.add(model_id)
            raise

    async def force_cleanup_process(self, model_id: str) -> dict | None:
        """
        Force cleanup an orphaned/broken worker process.

        Fallback when normal unload fails. Synchronous - no event wait.

        Returns:
            Dict with status or None if failed
        """
        if self._models is None:
            await self.connect()

        # Remove from state before cleanup (prevents stale routing)
        # If cleanup fails, we don't rollback because the model is likely
        # in a broken state anyway (orphaned process, no worker, etc.)
        self._ws_client._state._loaded_models.discard(model_id)

        return await self._models.force_cleanup_process(model_id)

    # =========================================================================
    # High-Level Methods
    # =========================================================================

    async def get_model_type(self, model_id: str) -> str:
        """Get model type for a model."""
        try:
            metadata = await self.fetch_model_info_dict(model_id)
            return metadata.get("model_type", "default") if metadata else "default"
        except Exception as e:
            logger.debug(f"Error getting model type for model {model_id}: {e}")
            return "default"

    async def should_apply_middleware(self, model_id: str) -> bool:
        """Determine if middleware should be applied to a model."""
        try:
            metadata = await self.fetch_model_info_dict(model_id)
            should_apply = should_apply_middleware_for_metadata(metadata)

            if metadata:
                mw_config = metadata.get("middleware_config", {})
                preserve = mw_config.get("preserve_personality", False)
                schema = metadata.get("input_schema", "prompt")
                mtype = metadata.get("model_type", "default")
                logger.debug(
                    f"Model {model_id}: preserve_personality={preserve}, "
                    f"input_schema={schema}, model_type={mtype}, "
                    f"should_apply={should_apply}"
                )

            return should_apply
        except Exception as e:
            logger.debug(
                f"Error determining if middleware should apply to {model_id}: {e}"
            )
            return False

    async def refresh_model_cache(self):
        """Refresh the model configuration cache."""
        try:
            models = await self.get_all_model_configurations()
            self._cache.refresh(models)
            logger.info(f"Refreshed model cache with {self._cache.size} models")
        except Exception as e:
            logger.debug(f"Error refreshing model cache: {e}")

    def get_cached_model_configuration(self, model_id: str) -> ModelMetadata | None:
        """Get cached model configuration."""
        return self._cache.get(model_id)
