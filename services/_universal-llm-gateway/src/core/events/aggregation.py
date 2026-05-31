"""
Event Aggregation - Compute insights and metrics from event streams.

Provides EventAggregator class for analyzing events to produce:
- Model usage statistics (load count, inference count per model)
- Performance metrics (avg inference duration, success/failure rates)
- Resource utilization trends
- Time-based aggregations
"""

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from universal_logging import get_logger

logger = get_logger(__name__)


@dataclass
class ModelStats:
    """Statistics for a single model"""

    model_id: str
    load_count: int = 0
    load_success_count: int = 0
    load_failure_count: int = 0
    unload_count: int = 0
    inference_count: int = 0
    inference_success_count: int = 0
    inference_failure_count: int = 0
    total_inference_duration: float = 0.0
    avg_inference_duration: float = 0.0
    last_loaded_timestamp: float | None = None
    last_inference_timestamp: float | None = None


@dataclass
class SystemStats:
    """System-wide statistics"""

    total_models_loaded: int = 0
    total_models_failed: int = 0
    total_inferences: int = 0
    total_inference_failures: int = 0
    avg_vram_usage_mb: float = 0.0
    avg_ram_usage_mb: float = 0.0
    peak_vram_usage_mb: int = 0
    peak_ram_usage_mb: int = 0


class EventAggregator:
    """
    Aggregates events to compute insights and statistics.

    Subscribes to events and maintains running statistics about:
    - Model usage patterns
    - Inference performance
    - Resource utilization
    - Success/failure rates

    Example:
        aggregator = EventAggregator(event_bus)

        # Get model statistics
        stats = aggregator.get_model_stats("llama-3-8b")
        print(f"Average inference time: {stats.avg_inference_duration}s")

        # Get system statistics
        sys_stats = aggregator.get_system_stats()
        print(f"Total inferences: {sys_stats.total_inferences}")
    """

    def __init__(self, event_bus, track_resource_history: bool = True):
        """
        Initialize event aggregator.

        Args:
            event_bus: EventBus to subscribe to
            track_resource_history: Whether to track historical resource data
        """
        self.event_bus = event_bus
        self.track_resource_history = track_resource_history

        # Statistics storage
        self.model_stats: dict[str, ModelStats] = defaultdict(
            lambda: ModelStats(model_id="")
        )
        self.system_stats = SystemStats()
        self.resource_history: list[dict[str, Any]] = []

        # Subscribe to events
        self._subscribe_to_events()

        logger.info("📊 EventAggregator initialized")

    def _subscribe_to_events(self):
        """Subscribe to relevant events using signal names"""
        # Import signal constants locally to avoid circular imports
        from .types import (
            INFERENCE_COMPLETED,
            INFERENCE_FAILED,
            INFERENCE_STARTED,
            MODEL_LOAD_FAILED,
            MODEL_LOADED,
            MODEL_LOADING_STARTED,
            MODEL_UNLOADED,
            MODEL_UNLOADING_STARTED,
            SYSTEM_RESOURCES_UPDATED,
        )

        # Model lifecycle events - subscribe by signal name (string)
        self.event_bus.subscribe_async(
            MODEL_LOADING_STARTED, self._handle_model_loading_started
        )
        self.event_bus.subscribe_async(MODEL_LOADED, self._handle_model_loaded)
        self.event_bus.subscribe_async(
            MODEL_LOAD_FAILED, self._handle_model_load_failed
        )
        self.event_bus.subscribe_async(
            MODEL_UNLOADING_STARTED, self._handle_model_unloading_started
        )
        self.event_bus.subscribe_async(MODEL_UNLOADED, self._handle_model_unloaded)

        # Inference lifecycle events - subscribe by signal name (string)
        self.event_bus.subscribe_async(
            INFERENCE_STARTED, self._handle_inference_started
        )
        self.event_bus.subscribe_async(
            INFERENCE_COMPLETED, self._handle_inference_completed
        )
        self.event_bus.subscribe_async(INFERENCE_FAILED, self._handle_inference_failed)

        # System resource events - subscribe by signal name (string)
        self.event_bus.subscribe_async(
            SYSTEM_RESOURCES_UPDATED, self._handle_system_resources_updated
        )

    def _handle_model_loading_started(self, event):
        """Handle model loading started event"""
        model_id = event.payload["model_id"]
        stats = self.model_stats[model_id]
        stats.model_id = model_id
        stats.load_count += 1

    def _handle_model_loaded(self, event):
        """Handle model loaded event"""
        model_id = event.payload["model_id"]
        stats = self.model_stats[model_id]
        stats.model_id = model_id
        stats.load_success_count += 1
        stats.last_loaded_timestamp = event.timestamp

        # Update system stats
        self.system_stats.total_models_loaded += 1

        # Track peak resource usage
        vram_usage_mb = event.payload["vram_usage_mb"]
        ram_usage_mb = event.payload["ram_usage_mb"]
        if vram_usage_mb > self.system_stats.peak_vram_usage_mb:
            self.system_stats.peak_vram_usage_mb = vram_usage_mb
        if ram_usage_mb > self.system_stats.peak_ram_usage_mb:
            self.system_stats.peak_ram_usage_mb = ram_usage_mb

    def _handle_model_load_failed(self, event):
        """Handle model load failed event"""
        model_id = event.payload["model_id"]
        stats = self.model_stats[model_id]
        stats.model_id = model_id
        stats.load_failure_count += 1

        # Update system stats
        self.system_stats.total_models_failed += 1

    def _handle_model_unloading_started(self, event):
        """Handle model unloading started event"""
        pass  # Currently no specific action needed

    def _handle_model_unloaded(self, event):
        """Handle model unloaded event"""
        model_id = event.payload["model_id"]
        stats = self.model_stats[model_id]
        stats.model_id = model_id
        stats.unload_count += 1

    def _handle_inference_started(self, event):
        """Handle inference started event"""
        pass  # Currently no specific action needed

    def _handle_inference_completed(self, event):
        """Handle inference completed event.

        Note: Lifecycle events are model-scoped. Duration tracking is not available
        from lifecycle events (use request-scoped events for detailed metrics).
        """
        model_id = event.payload["model_id"]
        stats = self.model_stats[model_id]
        stats.model_id = model_id
        stats.inference_count += 1
        stats.inference_success_count += 1
        # Note: duration not available in model-scoped lifecycle events
        stats.last_inference_timestamp = event.timestamp

        # Update system stats
        self.system_stats.total_inferences += 1

    def _handle_inference_failed(self, event):
        """Handle inference failed event"""
        model_id = event.payload["model_id"]
        stats = self.model_stats[model_id]
        stats.model_id = model_id
        stats.inference_count += 1
        stats.inference_failure_count += 1

        # Update system stats
        self.system_stats.total_inferences += 1
        self.system_stats.total_inference_failures += 1

    def _handle_system_resources_updated(self, event):
        """Handle system resources updated event"""
        # Extract from payload
        total_vram_mb = event.payload["total_vram_mb"]
        available_vram_mb = event.payload["available_vram_mb"]
        total_ram_mb = event.payload["total_ram_mb"]
        available_ram_mb = event.payload["available_ram_mb"]

        # Calculate average resource usage (running average)
        if self.system_stats.avg_vram_usage_mb == 0:
            self.system_stats.avg_vram_usage_mb = total_vram_mb - available_vram_mb
        else:
            # Simple moving average
            self.system_stats.avg_vram_usage_mb = (
                self.system_stats.avg_vram_usage_mb * 0.9
                + (total_vram_mb - available_vram_mb) * 0.1
            )

        if self.system_stats.avg_ram_usage_mb == 0:
            self.system_stats.avg_ram_usage_mb = total_ram_mb - available_ram_mb
        else:
            self.system_stats.avg_ram_usage_mb = (
                self.system_stats.avg_ram_usage_mb * 0.9
                + (total_ram_mb - available_ram_mb) * 0.1
            )

        # Track resource history
        if self.track_resource_history:
            self.resource_history.append(
                {
                    "timestamp": event.timestamp,
                    "vram_used_mb": total_vram_mb - available_vram_mb,
                    "ram_used_mb": total_ram_mb - available_ram_mb,
                    "vram_available_mb": available_vram_mb,
                    "ram_available_mb": available_ram_mb,
                }
            )

            # Keep only recent history (last 1000 samples)
            if len(self.resource_history) > 1000:
                self.resource_history = self.resource_history[-1000:]

    def get_model_stats(self, model_id: str) -> ModelStats | None:
        """
        Get statistics for a specific model.

        Args:
            model_id: Model identifier

        Returns:
            ModelStats object or None if model has no stats
        """
        if model_id in self.model_stats:
            return self.model_stats[model_id]
        return None

    def get_all_model_stats(self) -> dict[str, ModelStats]:
        """
        Get statistics for all models.

        Returns:
            Dictionary of model_id -> ModelStats
        """
        return dict(self.model_stats)

    def get_system_stats(self) -> SystemStats:
        """
        Get system-wide statistics.

        Returns:
            SystemStats object
        """
        return self.system_stats

    def get_top_models_by_inference_count(self, limit: int = 10) -> list[ModelStats]:
        """
        Get top N models by inference count.

        Args:
            limit: Number of models to return

        Returns:
            List of ModelStats sorted by inference count
        """
        sorted_models = sorted(
            self.model_stats.values(), key=lambda s: s.inference_count, reverse=True
        )
        return sorted_models[:limit]

    def get_top_models_by_avg_duration(self, limit: int = 10) -> list[ModelStats]:
        """
        Get top N models by average inference duration.

        Args:
            limit: Number of models to return

        Returns:
            List of ModelStats sorted by average duration
        """
        # Filter models with at least one inference
        models_with_inferences = [
            s for s in self.model_stats.values() if s.inference_success_count > 0
        ]

        sorted_models = sorted(
            models_with_inferences, key=lambda s: s.avg_inference_duration, reverse=True
        )
        return sorted_models[:limit]

    def get_model_success_rate(self, model_id: str) -> dict[str, float]:
        """
        Get success rates for a model.

        Args:
            model_id: Model identifier

        Returns:
            Dictionary with load_success_rate and inference_success_rate
        """
        stats = self.model_stats.get(model_id)
        if not stats:
            return {"load_success_rate": 0.0, "inference_success_rate": 0.0}

        load_success_rate = (
            stats.load_success_count / stats.load_count if stats.load_count > 0 else 0.0
        )

        inference_success_rate = (
            stats.inference_success_count / stats.inference_count
            if stats.inference_count > 0
            else 0.0
        )

        return {
            "load_success_rate": load_success_rate,
            "inference_success_rate": inference_success_rate,
        }

    def get_resource_history(
        self, since: float | None = None, limit: int | None = None
    ) -> list[dict[str, Any]]:
        """
        Get historical resource usage data.

        Args:
            since: Filter events after this timestamp
            limit: Maximum number of samples to return

        Returns:
            List of resource usage samples
        """
        history = self.resource_history

        if since:
            history = [h for h in history if h["timestamp"] >= since]

        if limit:
            history = history[-limit:]

        return history

    def reset_stats(self):
        """Reset all statistics"""
        self.model_stats.clear()
        self.system_stats = SystemStats()
        self.resource_history.clear()
        logger.info("📊 EventAggregator statistics reset")

    def export_stats(self) -> dict[str, Any]:
        """
        Export all statistics as dictionary.

        Returns:
            Dictionary with all aggregated statistics
        """
        return {
            "system_stats": {
                "total_models_loaded": self.system_stats.total_models_loaded,
                "total_models_failed": self.system_stats.total_models_failed,
                "total_inferences": self.system_stats.total_inferences,
                "total_inference_failures": self.system_stats.total_inference_failures,
                "avg_vram_usage_mb": self.system_stats.avg_vram_usage_mb,
                "avg_ram_usage_mb": self.system_stats.avg_ram_usage_mb,
                "peak_vram_usage_mb": self.system_stats.peak_vram_usage_mb,
                "peak_ram_usage_mb": self.system_stats.peak_ram_usage_mb,
            },
            "model_stats": {
                model_id: {
                    "model_id": stats.model_id,
                    "load_count": stats.load_count,
                    "load_success_count": stats.load_success_count,
                    "load_failure_count": stats.load_failure_count,
                    "unload_count": stats.unload_count,
                    "inference_count": stats.inference_count,
                    "inference_success_count": stats.inference_success_count,
                    "inference_failure_count": stats.inference_failure_count,
                    "avg_inference_duration": stats.avg_inference_duration,
                    "last_loaded_timestamp": stats.last_loaded_timestamp,
                    "last_inference_timestamp": stats.last_inference_timestamp,
                }
                for model_id, stats in self.model_stats.items()
            },
            "resource_history_count": len(self.resource_history),
        }
