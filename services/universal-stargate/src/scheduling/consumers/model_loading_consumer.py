"""
Model loading state consumer for tracking loading operations.

Subscribes to MODEL_LOADING_STARTED and MODEL_LOADING_FAILED events
to maintain loading state visibility and detect failures.
"""

from collections import defaultdict
from datetime import datetime

from universal_event_bus import Event, EventBus
from universal_logging import get_logger

from ..events import MODEL_LOADING_FAILED, MODEL_LOADING_STARTED

logger = get_logger(__name__)


class ModelLoadingConsumer:
    """
    Consumes model loading lifecycle events.

    Tracks which models are currently loading on which gateways,
    and records loading failures for diagnostics.
    """

    def __init__(self, event_bus: EventBus):
        self.event_bus = event_bus
        self._loading: dict[str, dict[str, datetime]] = defaultdict(dict)
        self._failures: list[dict] = []  # Recent failures (bounded)
        self._max_failures = 100

    def start(self) -> None:
        """Start consuming model loading events."""
        self.event_bus.subscribe_async(
            MODEL_LOADING_STARTED, self._handle_loading_started
        )
        self.event_bus.subscribe_async(
            MODEL_LOADING_FAILED, self._handle_loading_failed
        )
        logger.info("✅ ModelLoadingConsumer started")

    def stop(self) -> None:
        """Stop consuming events."""
        logger.info("ModelLoadingConsumer stopped")

    async def _handle_loading_started(self, event: Event) -> None:
        """Track model loading start."""
        payload = event.payload
        gateway_url = payload.get("url")
        model_id = payload.get("model_id")

        if not gateway_url or not model_id:
            return

        self._loading[gateway_url][model_id] = datetime.now()

        logger.info(f"🔄 Model loading started: {model_id} on {gateway_url}")

    async def _handle_loading_failed(self, event: Event) -> None:
        """Track model loading failure."""
        payload = event.payload
        gateway_url = payload.get("url")
        model_id = payload.get("model_id")
        error = payload.get("error", "Unknown error")

        if not gateway_url or not model_id:
            return

        # Remove from loading set
        if model_id in self._loading.get(gateway_url, {}):
            del self._loading[gateway_url][model_id]

        # Record failure
        self._failures.append(
            {
                "gateway_url": gateway_url,
                "model_id": model_id,
                "error": error,
                "timestamp": datetime.now().isoformat(),
            }
        )

        # Bound failure list
        if len(self._failures) > self._max_failures:
            self._failures = self._failures[-self._max_failures :]

        logger.warning(f"❌ Model loading failed: {model_id} on {gateway_url}: {error}")

    def get_loading_models(self) -> dict[str, list[str]]:
        """Get currently loading models by gateway."""
        return {
            gateway: list(models.keys())
            for gateway, models in self._loading.items()
            if models
        }

    def get_recent_failures(self, limit: int = 10) -> list[dict]:
        """Get recent loading failures."""
        return self._failures[-limit:]
