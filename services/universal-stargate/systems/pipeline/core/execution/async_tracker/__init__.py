"""Async pipeline execution tracker.

In-process record store for pipelines dispatched via
``POST /api/v1/pipelines/dispatch``. The tracker is the sole writer of
dispatch-lifecycle signals — route handlers and the background wrapper drive
transitions, but the signal emission is centralized here so observability
is independent of the caller.

Invariants:
- ∀ ``register_execution`` success: emit ``pipeline.dispatch.async`` once.
- ∀ terminal transition (``complete_execution`` / ``fail_execution``):
  emit ``pipeline.dispatch.completed`` exactly once (idempotent guard).
  Note: for ``op="to_thread"`` records, ``_run_delivery_with_outcome``
  may demote a ``completed`` record to ``failed`` after the on-behalf
  POST fails (architectural decision dispatch-to-thread-delivery-2026-05-22
  §2.1). The demote emits a second ``pipeline.dispatch.completed`` with
  ``status="failed"`` for the same execution_id. Consumers of the event
  signal should expect up to two emissions per to_thread execution and
  key off the latest ``status``.
- ∀ admission refusal: emit ``pipeline.dispatch.rejected`` before raising.
- TTL pruning uses ``completed_at_monotonic`` — running records are never
  evicted by age alone, only by explicit admission-time capacity pressure
  against terminal records.
- Optional journal hook persists terminal records out-of-process without
  blocking tracker transitions.

Records are node-local and non-durable across Stargate restart. Callers that
require durable result delivery must use the ``result_delivery`` hook
rather than polling after a restart.

Package layout (modularized from the former single ``async_tracker.py`` module;
the public import path is unchanged — import the names below from
``...core.execution.async_tracker``):
- ``constants`` — capacity / retention tunables and the ISO-8601 timestamp
  helper.
- ``errors`` — ``TrackerCapacityError`` (mapped to HTTP 503 by the route).
- ``protocol`` — the minimal ``_EventBusProtocol`` the tracker emits through.
- ``records`` — ``PipelineExecutionResult`` / ``PipelineExecutionError`` /
  ``PipelineExecutionRecord`` dataclasses and ``to_dict`` serialization.
- ``tracker`` — the ``PipelineExecutionTracker`` class: instance state plus the
  thin public delegators.
- ``lifecycle`` — ``register_execution`` / ``complete_execution`` /
  ``fail_execution`` transition logic.
- ``queries`` — ``get_record`` / ``wait_for_terminal`` lookups.
- ``tracker_events`` — the fire-and-forget ``_emit`` sync/async publish bridge.
- ``delivery_hooks`` — bus-delivery scheduling and outcome-driven demotion.
- ``dispatch_admit`` — the agent-bus dispatch-admit side effect.
- ``journal`` — the out-of-process journaling hook.
- ``prune`` — TTL pruning of terminal records.
"""

from __future__ import annotations

from .errors import TrackerCapacityError
from .records import (
    PipelineExecutionError,
    PipelineExecutionRecord,
    PipelineExecutionResult,
)
from .tracker import PipelineExecutionTracker

__all__ = [
    "PipelineExecutionError",
    "PipelineExecutionRecord",
    "PipelineExecutionResult",
    "PipelineExecutionTracker",
    "TrackerCapacityError",
]
