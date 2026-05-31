"""Map-step execution: delegation to ``MapExecutor`` fan-out.

Owns ``execute_map_step``. MAP is an execution mode, not a handler type:
``step.type`` carries the real handler type (e.g. ``"generate"``) and
``type="map"`` is rejected at parse time by ``StepConfig.reject_map_type``.
Builds the iteration handler, namespace resolver, and proxy-client cancel
callback, runs ``MapExecutor.execute()``, stores the collection on the context,
and returns a summarizing ``StepOutput`` with map latency. Imports of the map
machinery are kept lazy (as in the monolith) to avoid an import cycle through
``execution.map_reduce``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ....dag import StepNode
    from ....handlers.protocol import StepOutput
    from .dag_executor import DAGExecutor


async def execute_map_step(executor: DAGExecutor, node: StepNode) -> StepOutput:
    """
    Execute map step with MapExecutor.

    MAP is an execution mode, not a handler type. The step.type field
    contains the actual handler type (e.g., "generate"). type="map"
    is rejected at parse time by StepConfig.reject_map_type validator.
    """
    import time

    from ....handlers import HandlerRegistry
    from ....handlers.protocol import StepOutput
    from ...map_reduce import MapExecutor
    from ...resolver import NamespaceResolver

    step = node.step
    handler = HandlerRegistry.create_handler(
        executor.context.domain,
        step.type,
        variant=executor.context.pipeline.source_variant,
    )
    resolver = NamespaceResolver(executor.context)
    proxy_client = await executor._ensure_proxy_client()

    executor_inst = MapExecutor(
        step=step,
        handler=handler,
        resolver=resolver,
        runtime=executor.context,
        checkpoint_manager=executor._checkpoint_manager,
        cancel_callback=proxy_client.cancel,
    )

    start_time = time.time()
    collection = await executor_inst.execute()
    latency_ms = (time.time() - start_time) * 1000

    executor.context.set_output(step.id, collection)
    return StepOutput(
        raw=f"Map step completed with {len(collection)} outputs",
        json={"outputs": [o.json for o in collection.all_outputs()]},
        latency_ms=latency_ms,
    )
