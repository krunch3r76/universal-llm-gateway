"""Map iteration runtime and handler protocol definitions.

Defines the structural contracts that the map step fan-out executor needs from
its collaborating runtime and per-iteration handler. These protocols isolate
``MapExecutor`` from concrete pipeline runtime / handler types so the executor
can be unit-tested against minimal stand-ins, while still allowing the
production runtime and handler implementations to satisfy the contract by duck
typing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, Self

if TYPE_CHECKING:
    from .....schemas import StepConfig


class MapIterationRuntimeProtocol(Protocol):
    """Structural runtime contract that ``MapExecutor`` and per-iteration execution
    require.

    Exposes pipeline, execution_id, recorder and the private ``_proxy`` (event bus
    source), plus immutable ``with_*`` builders returning a decorated copy scoped to one
    iteration: map iteration request ID, inference request ID and map state. Satisfied
    by the production pipeline runtime via duck typing.
    """

    pipeline: Any  # TODO: tighten to concrete runtime pipeline protocol
    execution_id: str
    recorder: Any  # TODO: tighten to concrete recorder protocol
    _proxy: Any  # TODO: tighten to concrete proxy protocol

    def with_map_iteration_request_id(self, request_id: str) -> Self: ...

    def with_inference_request_id(self, request_id: str) -> Self: ...

    def with_map_state(self, map_state: Any) -> Self: ...


class MapIterationHandlerProtocol(Protocol):
    """Structural contract for the step handler that ``MapExecutor`` fans out over.

    ``execute(step, context)`` is awaited once per map iteration with the per-iteration
    ``StepConfig`` (model and map inputs applied) and the iteration-scoped runtime; the
    returned step output is collected into the ``MapOutputCollection``.
    """

    async def execute(self, step: StepConfig, _context: Any) -> Any: ...
