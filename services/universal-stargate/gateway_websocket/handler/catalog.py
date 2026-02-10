"""Handler for catalog update events."""

from typing import Any

from universal_logging import get_logger

from .base import SyncMessageHandler
from .context import HandlerContext

logger = get_logger(__name__)


class CatalogUpdateHandler(SyncMessageHandler):
    """
    Handle CATALOG_UPDATE message.

    State: update _models, _catalog
    Side effect: callback notification (fire-and-forget)

    CRITICAL: Callback is fire-and-forget (scheduled via create_task).
    We do NOT await callbacks to avoid blocking the WebSocket receive loop.

    If callback ordering is required, the callback itself should use
    an internal queue pattern.
    """

    def handle(self, data: dict[str, Any], ctx: HandlerContext) -> None:
        reason = data.get("reason", "unknown")
        models = data.get("models")
        catalog = data.get("catalog")

        if models is not None:
            ctx.models.clear()
            ctx.models.update(models)
            logger.info(f"Catalog update: {len(ctx.models)} models (reason: {reason})")
        else:
            logger.info(f"Catalog update notification: {reason} (no models data)")

        if catalog is not None:
            ctx.catalog.clear()
            ctx.catalog.update(catalog)
            logger.debug("Catalog data updated")

        # Notify Stargate (fire-and-forget - do NOT await)
        if ctx.on_catalog_update and catalog is not None:
            ctx.schedule_callback(ctx.on_catalog_update, (catalog,))
