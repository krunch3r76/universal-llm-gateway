"""Handler for request-scoped inference start telemetry messages."""

from typing import Any, TypedDict, cast, override

from universal_logging import get_logger

from .base import SyncMessageHandler
from .context import HandlerContext

logger = get_logger(__name__)


class RequestInferenceStartedPayload(TypedDict, total=False):
    """Typed payload contract for request inference start telemetry."""

    request_id: str
    model_id: str
    gateway_url: str
    correlation_id: str | None


class RequestInferenceStartedHandler(SyncMessageHandler):
    """Handle REQUEST_INFERENCE_STARTED telemetry from Gateway."""

    @override
    def handle(self, data: dict[str, Any], ctx: HandlerContext) -> None:
        payload = cast(RequestInferenceStartedPayload, data)
        request_id = payload.get("request_id")
        model_id = payload.get("model_id")
        gateway_url = payload.get("gateway_url") or ctx.gateway_http_url
        if not request_id or not model_id:
            logger.error(
                "REQUEST_INFERENCE_STARTED missing required identity fields: "
                "request_id/model_id"
            )
            return

        if not payload.get("gateway_url"):
            logger.warning(
                "REQUEST_INFERENCE_STARTED missing gateway_url; "
                "using handler context gateway_http_url=%s",
                ctx.gateway_http_url,
            )

        if ctx.on_request_inference_started:
            ctx.schedule_callback(
                ctx.on_request_inference_started,
                (
                    request_id,
                    model_id,
                    gateway_url,
                    payload.get("correlation_id"),
                ),
            )
