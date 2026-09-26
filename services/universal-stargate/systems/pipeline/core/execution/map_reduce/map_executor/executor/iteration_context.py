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
    """Seed one correlation dict per map iteration before fan-out begins.

    For each ``(index, value, key)`` item, prepares inputs and a per-iteration step to
    learn the resolved model (``model_ref`` or ``model_id``, honoring
    ``pool_assignments``), then records ``model_id``, ``gateway_id`` (None), a monotonic
    ``started_at`` and fresh UUID ``map_iteration_request_id`` / ``request_id``. Returns
    ``{index: ctx}``; used for event correlation and by ``MapConcurrencyManager`` to
    cancel federation requests.
    """
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
    """Derive an iteration-scoped runtime carrying the map and inference request IDs.

    Applies ``with_map_iteration_request_id`` and ``with_inference_request_id`` to the
    executor's shared runtime using the IDs seeded by ``build_iteration_context``; each
    is skipped if absent from ``ctx``. The shared runtime is not mutated; the decorated
    copy is returned for ``execute_iteration`` to hand to the step handler.
    """
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
