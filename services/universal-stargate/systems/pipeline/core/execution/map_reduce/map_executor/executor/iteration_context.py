"""Per-iteration context and runtime decoration for map step execution.

Builds the per-iteration context dict that carries correlation IDs, timing
checkpoints, and resolved model identity through the lifetime of a single map
iteration, and decorates the shared runtime with iteration-scoped request IDs
so downstream telemetry and event subscriptions can disambiguate concurrent
iterations.
"""

from __future__ import annotations

import time
import uuid
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .map_executor import MapExecutor
    from .protocols import MapIterationRuntimeProtocol


def build_iteration_context(
    executor: MapExecutor,
    *,
    iteration_items: list[tuple[int, object, str | None]],
    pool_assignments: dict[int, str],
    total: int,
) -> dict[int, dict[str, Any]]:
    """Build and seed per-iteration context used by event correlation."""
    iteration_context: dict[int, dict[str, Any]] = {}
    for idx, value, key in iteration_items:
        assigned_model = pool_assignments.get(idx)
        iter_inputs = executor._iteration_preparer.prepare_iteration_inputs(
            idx, value, total, key, assigned_model
        )
        iter_step = executor._iteration_preparer.create_iteration_step(
            iter_inputs[2], assigned_model
        )
        model_id_for_iteration = getattr(
            iter_step, "model_ref", getattr(iter_step, "model_id", None)
        )
        iteration_context[idx] = {
            "model_id": model_id_for_iteration,
            "gateway_id": None,
            "started_at": time.monotonic(),
            "map_iteration_request_id": str(uuid.uuid4()),
            "request_id": str(uuid.uuid4()),
        }
    return iteration_context


def build_iteration_runtime(
    executor: MapExecutor,
    ctx: dict[str, Any],
) -> MapIterationRuntimeProtocol:
    """Decorate runtime with iteration and request-level correlation IDs."""
    iter_runtime = executor._runtime
    map_iteration_request_id = ctx.get("map_iteration_request_id")
    if map_iteration_request_id:
        iter_runtime = iter_runtime.with_map_iteration_request_id(
            map_iteration_request_id
        )
    inference_request_id = ctx.get("request_id")
    if inference_request_id:
        iter_runtime = iter_runtime.with_inference_request_id(inference_request_id)
    return iter_runtime
