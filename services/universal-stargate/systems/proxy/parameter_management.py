import time
from typing import TYPE_CHECKING, Any, Optional

from universal_logging import get_logger

if TYPE_CHECKING:
    from gateways import GatewayInstance

logger = get_logger(__name__)


class ParameterManager:
    """Parameter management functionality for the middleware proxy"""

    def __init__(self, gateway_url: str):
        self.gateway_url = gateway_url
        self._model_defaults_cache = {}
        self._supported_params_cache = {}
        self._cache_timestamp = 0
        self._cache_ttl = 300  # 5 minutes
        self.http_client = None

    async def set_http_client(self, http_client):
        """Set the HTTP client for parameter requests"""
        self.http_client = http_client

    async def get_cached_model_defaults(
        self, model_id: str, gateway_instance: Optional["GatewayInstance"] = None
    ) -> dict[str, Any]:
        """Get cached model defaults, refreshing if needed"""
        current_time = time.time()

        # Select gateway and client
        if gateway_instance:
            http_client = gateway_instance.client.get_http_client()
            gateway_url = gateway_instance.config.base_url
        else:
            http_client = self.http_client
            gateway_url = self.gateway_url

        # Check if cache is valid
        if (
            current_time - self._cache_timestamp > self._cache_ttl
            or model_id not in self._model_defaults_cache
        ):
            try:
                # Query gateway for model defaults
                response = await http_client.get(
                    f"{gateway_url}/api/v1/model_info/{model_id}"
                )
                if response.status_code == 200:
                    data = response.json()
                    self._model_defaults_cache[model_id] = data["backend_defaults"]
                    self._cache_timestamp = current_time
                    logger.debug(f"Cached model defaults for {model_id}")
                else:
                    logger.warning(
                        f"Failed to get model defaults for {model_id}: {response.status_code}"
                    )
                    self._model_defaults_cache[model_id] = {}
            except Exception as e:
                logger.warning(f"Error getting model defaults for {model_id}: {e}")
                self._model_defaults_cache[model_id] = {}

        return self._model_defaults_cache.get(model_id, {})

    async def _get_cached_supported_parameters(
        self, model_id: str, gateway_instance: Optional["GatewayInstance"] = None
    ) -> dict[str, list[str]]:
        """Get cached supported parameters, refreshing if needed"""
        current_time = time.time()

        # Select gateway and client
        if gateway_instance:
            http_client = gateway_instance.client.get_http_client()
            gateway_url = gateway_instance.config.base_url
        else:
            http_client = self.http_client
            gateway_url = self.gateway_url

        # Check if cache is valid
        if (
            current_time - self._cache_timestamp > self._cache_ttl
            or model_id not in self._supported_params_cache
        ):
            try:
                # Query gateway for supported parameters
                response = await http_client.get(
                    f"{gateway_url}/api/v1/model_info/{model_id}"
                )
                if response.status_code == 200:
                    data = response.json()
                    self._supported_params_cache[model_id] = data[
                        "supported_parameters"
                    ]
                    self._cache_timestamp = current_time
                    logger.debug(f"Cached supported parameters for {model_id}")
                else:
                    logger.warning(
                        f"Failed to get supported parameters for {model_id}: {response.status_code}"
                    )
                    self._supported_params_cache[model_id] = {"all": []}
            except Exception as e:
                logger.warning(
                    f"Error getting supported parameters for {model_id}: {e}"
                )
                self._supported_params_cache[model_id] = {"all": []}

        return self._supported_params_cache.get(model_id, {"all": []})

    def _compare_parameters(
        self,
        user_params: dict[str, Any],
        model_defaults: dict[str, Any],
        final_params: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Compare user parameters, model defaults, and final parameters"""
        parameter_changes = []

        for param, final_value in final_params.items():
            user_value = user_params.get(param)
            default_value = model_defaults.get(param)

            # Determine if parameter was modified
            modified = False
            source = "user_set"

            if param not in user_params:
                # Parameter was not set by user - came from defaults
                source = "model_default"
                if default_value != final_value:
                    # Default was overridden by other defaults
                    source = "other_default"
                    modified = True
            elif user_value != final_value:
                # User parameter was modified (e.g., by token management)
                modified = True
                source = "middleware_modified"

            # Track detailed changes
            parameter_changes.append(
                {
                    "parameter": param,
                    "user_value": user_value,
                    "default_value": default_value,
                    "final_value": final_value,
                    "source": source,
                    "modified": modified,
                    "change_type": self._get_change_type(
                        user_value, default_value, final_value
                    ),
                }
            )

        return parameter_changes

    def _log_parameter_comparison(
        self,
        model_id: str,
        user_params: dict[str, Any],
        model_defaults: dict[str, Any],
        final_params: dict[str, Any],
        parameter_changes: list[dict[str, Any]],
        start_time: float,
    ):
        """Log parameter comparison details"""
        processing_time = (time.time() - start_time) * 1000

        # Count parameter types
        total_params = len(final_params)
        user_set_params = len(user_params)
        default_applied_params = len(
            [p for p in parameter_changes if p["source"] == "model_default"]
        )
        modified_params = len([p for p in parameter_changes if p["modified"]])

        # Log summary
        logger.info(f"Parameter comparison for {model_id}:")
        logger.info(f"  Total parameters: {total_params}")
        logger.info(f"  User set: {user_set_params}")
        logger.info(f"  Defaults applied: {default_applied_params}")
        logger.info(f"  Modified: {modified_params}")
        logger.info(f"  Processing time: {processing_time:.2f}ms")

        # Log detailed changes
        for change in parameter_changes:
            if change["modified"] or change["source"] != "user_set":
                logger.debug(
                    f"  {change['parameter']}: {change['user_value']} → {change['final_value']} ({change['source']})"
                )
