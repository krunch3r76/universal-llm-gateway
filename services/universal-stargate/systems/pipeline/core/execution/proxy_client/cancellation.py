"""ProxyClient cancel operation mixin.

Sends cancel_group requests to Stargate's /api/v1/pipeline/cancel endpoint
so that all in-flight requests for a map iteration can be aborted.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from universal_logging import get_logger

if TYPE_CHECKING:
    from .configuration import ProxyClientConfig

logger = get_logger(__name__)


class _ProxyCancellation:
    """Mixin providing cancel() for ProxyClient."""

    _config: ProxyClientConfig

    async def cancel(
        self, map_iteration_request_id: str, model_id: str | None = None
    ) -> bool:
        """Cancel a cancel group by map_iteration_request_id.

        Sends cancel_group to Stargate, which cancels all requests
        registered under this group (all calls within one map iteration).
        """
        client = await self._ensure_client()

        try:
            body: dict[str, str] = {"cancel_group": map_iteration_request_id}
            if model_id:
                body["model_id"] = model_id

            response = await client.post(
                "/api/v1/pipeline/cancel",
                json=body,
                timeout=5.0,  # Short timeout for cancel
            )

            if response.status_code == 200:
                data = response.json()
                return data.get("cancelled", False)
            elif response.status_code == 503:
                logger.warning("Proxy not initialized for cancel")
                return False
            else:
                logger.warning(
                    "Cancel returned %s: %s",
                    response.status_code,
                    response.text,
                )
                return False

        except Exception as e:
            logger.error(
                "Cancel failed for %s...: %s",
                map_iteration_request_id[:8],
                e,
            )
            return False
