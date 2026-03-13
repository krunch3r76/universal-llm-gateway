"""Federated non-streaming routing package exports."""

from .errors import _build_constraint_summary
from .events import (
    _emit_event_safe,
    _emit_eviction_classification_event,
    _emit_overflow_assigned_event,
    _emit_overflow_failed_event,
    _emit_overflow_load_started_event,
    _emit_overflow_triggered_event,
    _emit_routing_model_infeasible_event,
    _emit_routing_resource_gap_event,
)
from .handler import _route_to_federated_gateway
from .wait_logic import _eviction_wait_queue_depth, _wait_and_retry_selection

__all__ = [
    "_emit_event_safe",
    "_emit_routing_resource_gap_event",
    "_emit_routing_model_infeasible_event",
    "_emit_eviction_classification_event",
    "_emit_overflow_triggered_event",
    "_emit_overflow_failed_event",
    "_emit_overflow_load_started_event",
    "_emit_overflow_assigned_event",
    "_build_constraint_summary",
    "_eviction_wait_queue_depth",
    "_wait_and_retry_selection",
    "_route_to_federated_gateway",
]
