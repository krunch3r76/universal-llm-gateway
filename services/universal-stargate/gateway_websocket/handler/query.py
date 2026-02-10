"""Handler for query response events."""

from typing import Any

from universal_logging import get_logger

from .base import SyncMessageHandler
from .context import HandlerContext

logger = get_logger(__name__)


class QueryResponseHandler(SyncMessageHandler):
    """
    Handle RESPONSE message (query response).

    State: resolve pending query future
    """

    def handle(self, data: dict[str, Any], ctx: HandlerContext) -> None:
        request_id = data.get("request_id")
        if not request_id:
            logger.debug("RESPONSE missing request_id")
            return

        if request_id in ctx.pending_queries:
            future = ctx.pending_queries.pop(request_id)
            future.set_result(data)
        else:
            logger.debug(f"RESPONSE for unknown request_id: {request_id}")
