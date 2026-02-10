"""Model management operations via HTTP.

HTTP is used ONLY for commands (load/unload).
Status queries use WebSocket via GatewayClient.
"""

import httpx
from universal_logging import get_logger

from .http_methods import HTTPMethods

logger = get_logger(__name__)


class ModelManagement:
    """Model load/unload commands via HTTP.

    Commands only - status queries use WebSocket in GatewayClient:
    - client.get_loaded_models() for loaded model set
    - client.get_resource_status() for resource data
    """

    def __init__(self, http_methods: HTTPMethods):
        self._http = http_methods

    async def load_model(self, model_id: str) -> bool:
        """Load a specific model on this gateway."""
        try:
            response = await self._http.request(
                "POST", f"/api/v1/models/{model_id}/load"
            )
            if response:
                logger.info(f"✅ Model {model_id} loading initiated successfully")
                return True
            else:
                logger.debug(f"Failed to initiate loading of model {model_id}")
                return False
        except Exception as e:
            logger.debug(f"Error loading model {model_id}: {e}")
            return False

    async def unload_model(self, model_id: str, force: bool = False) -> bool:
        """Unload a model from the gateway.

        Args:
            model_id: Model to unload
            force: If True, kill process immediately (for eviction)

        Returns:
            True if unload initiated successfully
            False if unload was skipped or failed

        Note: True means unload was INITIATED, not completed.
        Caller should wait for MODEL_UNLOADED event for confirmation.
        """
        try:
            url = f"/api/v1/models/{model_id}"
            if force:
                url += "?force=true"

            response = await self._http.request("DELETE", url)
            if not response:
                logger.debug(f"Failed to unload model {model_id}: no response")
                return False

            # Check actual status field, not just HTTP 200
            status = response.get("status")
            reason = response.get("reason", "")

            if status == "unloaded":
                logger.info(f"Unload initiated for {model_id} (force={force})")
                return True
            elif status == "not_loaded":
                logger.debug(f"Model {model_id} was not loaded")
                return True  # Goal achieved
            elif status == "skipped":
                logger.warning(f"Model {model_id} unload skipped: {reason}")
                return False
            else:
                logger.warning(f"Unknown unload status '{status}' for {model_id}")
                return False
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 409:
                logger.debug(f"Cannot unload model {model_id}: model is busy")
                return False
            elif e.response.status_code == 404:
                logger.debug(f"Cannot unload model {model_id}: model not found")
                return False
            else:
                logger.debug(
                    f"Failed to unload model {model_id}: HTTP {e.response.status_code}"
                )
                return False
        except Exception as e:
            logger.debug(f"Error unloading model {model_id}: {e}")
            return False

    async def force_cleanup_process(self, model_id: str) -> dict | None:
        """
        Force cleanup an orphaned/broken worker process.

        POST /api/v1/models/{model_id}/cleanup

        Fallback when normal unload fails. Synchronous - no event wait.
        """
        try:
            response = await self._http.request(
                "POST",
                f"/api/v1/models/{model_id}/cleanup",
                timeout=5.0,
            )
            if response:
                status = response.get("status")
                if status in ("cleaned", "no_process"):
                    return response
                logger.warning(f"Unexpected cleanup status '{status}' for {model_id}")
                return response
            return None
        except Exception as e:
            logger.warning(f"Force cleanup API call failed for {model_id}: {e}")
            return None
