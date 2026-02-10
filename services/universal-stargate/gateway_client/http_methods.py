"""HTTP methods for gateway API access."""

import asyncio
from typing import Any
from urllib.parse import urljoin

import httpx
from universal_logging import get_logger

from .config import ModelMetadata

logger = get_logger(__name__)


class HTTPMethods:
    """HTTP request methods for gateway API.

    Provides retry logic, model metadata fetching, and catalog operations.
    All HTTP requests to the gateway flow through this class.
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        config,
        event_bus=None,
    ):
        self._client = client
        self.base_url = base_url
        self.config = config
        self._event_bus = event_bus

    def set_client(self, client: httpx.AsyncClient) -> None:
        """Update the HTTP client instance."""
        self._client = client

    async def request(self, method: str, path: str, **kwargs) -> dict[str, Any]:
        """Make HTTP request with retry logic."""
        url = (
            path
            if path.startswith("http")
            else urljoin(f"{self.base_url}/", path.lstrip("/"))
        )

        for attempt in range(self.config.max_retries + 1):
            try:
                if not self._client:
                    raise RuntimeError("HTTP client not initialized")

                response = await self._client.request(method, url, **kwargs)
                response.raise_for_status()
                return response.json()

            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    logger.debug(f"Resource not found: {url}")
                    return {}
                elif attempt == self.config.max_retries:
                    raise
                else:
                    await self._publish_retry_event(method, url, attempt, e)
                    await asyncio.sleep(self.config.retry_delay * (2**attempt))

            except Exception as e:
                if attempt == self.config.max_retries:
                    raise
                else:
                    await self._publish_retry_event(method, url, attempt, e)
                    await asyncio.sleep(self.config.retry_delay * (2**attempt))

        return {}

    async def _publish_retry_event(
        self, method: str, url: str, attempt: int, error: Exception
    ) -> None:
        """Publish retry event for structured monitoring (fire-and-forget)."""
        if not self._event_bus:
            return

        import asyncio

        # Import here to avoid circular dependency

        backoff_ms = int(self.config.retry_delay * (2**attempt) * 1000)
        # Fire-and-forget: schedule task without blocking
        from src.scheduling.events import GatewayRetryAttempted

        asyncio.create_task(
            self._event_bus.publish_async_nowait(
                GatewayRetryAttempted(
                    gateway_url=self.base_url,
                    method=method,
                    path=url,
                    attempt=attempt + 1,
                    max_retries=self.config.max_retries,
                    error_type=type(error).__name__,
                    error_message=str(error),
                    backoff_delay_ms=backoff_ms,
                )
            )
        )

    async def fetch_model_info_dict(
        self, model_id: str, include_all_fields: bool = False
    ) -> dict[str, Any] | None:
        """Fetch raw model info dict (OpenAI-ish model object) from Gateway.

        Returns arbitrary fields from Gateway's model catalog. Use this for
        features requiring fields not in ModelMetadata (e.g., personality config).

        For typed resource requirements (vram_usage, ram_usage), use
        fetch_model_configuration() instead.
        """
        try:
            url = f"/v1/models/{model_id}"
            if include_all_fields:
                url += "?include_all_fields=true"

            response = await self.request("GET", url)
            if response:
                if "data" in response and len(response["data"]) > 0:
                    return response["data"][0]
                else:
                    return response
            return None
        except Exception as e:
            logger.debug(f"Error getting model info for {model_id}: {e}")
            return None

    async def fetch_model_configuration(self, model_id: str) -> ModelMetadata | None:
        """Fetch typed model configuration (ModelMetadata) from Gateway.

        Returns ModelMetadata with typed fields (vram_usage, ram_usage, format, etc).
        Use this for resource requirements and routing decisions.

        For arbitrary fields (e.g., personality config), use fetch_model_info_dict().

        Only returns models whose files are available on the gateway
        (available_only=true). This prevents routing to models that
        cannot be loaded.
        """
        url = f"/v1/models/{model_id}?include_all_fields=true&available_only=true"
        try:
            logger.debug(f"🔍 HTTP GET {url}")
            response = await self.request("GET", url)

            if response and "data" in response and len(response["data"]) > 0:
                model_data = response["data"][0]
                logger.debug(f"✅ Model {model_id} found in gateway response")
                return ModelMetadata.from_api_response(model_data)

            # Empty response - log what we got
            logger.info(
                f"⚠️ Model {model_id} query returned empty/no data "
                f"(response keys: {list(response.keys()) if response else 'None'})"
            )
            return None

        except Exception as e:
            logger.warning(
                f"❌ HTTP error getting model {model_id}: {type(e).__name__}: {e}"
            )
            return None

    async def get_all_model_configurations(self) -> dict[str, ModelMetadata]:
        """Get configurations for all models at once."""
        try:
            response = await self.request("GET", "/api/v1/model_info/configurations")
            if response and "models" in response:
                models = {}
                for model_id, model_data in response["models"].items():
                    models[model_id] = ModelMetadata.from_api_response(model_data)
                return models
            return {}
        except Exception as e:
            logger.debug(f"Error getting all model configurations: {e}")
            return {}

    async def get_model_chat_template(self, model_id: str) -> dict[str, Any]:
        """Get chat template information for a model."""
        try:
            response = await self.request("GET", f"/api/v1/model_info/{model_id}")
            return response
        except Exception as e:
            logger.debug(f"Error getting chat template for model {model_id}: {e}")
            return {}

    async def get_model_parameter_defaults(self, model_id: str) -> dict[str, Any]:
        """Get parameter defaults for a model."""
        try:
            response = await self.request("GET", f"/api/v1/model_info/{model_id}")
            return response.get("backend_defaults", {})
        except Exception as e:
            logger.debug(f"Error getting parameter defaults for model {model_id}: {e}")
            return {}

    async def get_supported_parameters(self, model_id: str) -> list[str]:
        """Get supported parameters for a model."""
        try:
            response = await self.request("GET", f"/api/v1/model_info/{model_id}")
            return response.get("supported_parameters", {}).get("all", [])
        except Exception as e:
            logger.debug(
                f"Error getting supported parameters for model {model_id}: {e}"
            )
            return []

    async def get_catalog(self, include_models: bool = False) -> dict[str, Any]:
        """Get the full model catalog from Gateway."""
        try:
            url = "/api/v1/catalog"
            if not include_models:
                url += "?include_models=false"
            response = await self.request("GET", url)
            return response if response else {}
        except Exception as e:
            logger.debug(f"Error fetching catalog: {e}")
            return {}
