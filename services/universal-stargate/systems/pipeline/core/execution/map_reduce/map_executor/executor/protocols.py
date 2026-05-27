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
    """Runtime contract needed by map iteration execution paths."""

    pipeline: Any  # TODO: tighten to concrete runtime pipeline protocol
    execution_id: str
    recorder: Any  # TODO: tighten to concrete recorder protocol
    _proxy: Any  # TODO: tighten to concrete proxy protocol

    def with_map_iteration_request_id(self, request_id: str) -> Self: ...

    def with_inference_request_id(self, request_id: str) -> Self: ...

    def with_map_state(self, map_state: Any) -> Self: ...


class MapIterationHandlerProtocol(Protocol):
    """Handler contract needed by map executor."""

    async def execute(self, step: StepConfig, _context: Any) -> Any: ...
