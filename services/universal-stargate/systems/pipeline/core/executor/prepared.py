"""Shared prepared-state, protocols, and named loggers for the executor package.

Foundational layer of the executor package — every other module imports
from here. Owns:

- ``PreparedPipelineExecution`` — dataclass that carries resolved DAG
  state between sync and async entry points so both
  ``/v1/chat/completions`` and ``/api/v1/pipelines/dispatch`` execute
  the same DAG without re-preparation.
- ``_RequestExecutorProtocol`` / ``_PipelineRequestContextProtocol`` —
  minimal duck-typed contracts so helper modules can type-annotate
  without importing proxy or chat-completion machinery at runtime.
- ``execution_logger`` — shared ``systems.pipeline.execution`` named
  logger used by preparation, execution_loop, and outcome_assembly.
  Defined once here so every emission shares the same logger name.

Invariants:
- ``PreparedPipelineExecution`` is constructed exactly once per
  execution (in ``preparation.do_prepare_execution``) and consumed by
  exactly one of the two entry points before its ``recorder`` is closed.
- ``execution_logger`` is name-keyed (``"systems.pipeline.execution"``),
  not ``__name__``-keyed — re-defining it elsewhere would defeat the
  single source of truth.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from universal_logging import get_logger

from ..events import EventRecorder
from ..execution import DAGExecutor
from ..handlers import PipelineContext
from ..schemas import PipelineSpec, StepConfig

if TYPE_CHECKING:
    from ..execution.async_tracker import PipelineExecutionTracker  # noqa: F401

logger = get_logger(__name__)
execution_logger = get_logger("systems.pipeline.execution")


class _RequestExecutorProtocol(Protocol):
    """Minimal contract for request execution (avoids importing proxy at runtime)."""

    async def execute_request(self, context: Any) -> Any: ...


class _PipelineRequestContextProtocol(Protocol):
    """Context passed to execute(); has http_request, original_request, chat_request."""

    http_request: Any
    original_request: dict[str, Any] | None
    chat_request: Any


@dataclass(slots=True, kw_only=True)
class PreparedPipelineExecution:
    """Shared prepared state consumed by sync and async pipeline entrypoints.

    Holds the resolved pipeline spec, DAG context, node map, extracted input
    text, and DAG executor so both ``/v1/chat/completions`` and
    ``/api/v1/pipelines/dispatch`` execute the same DAG without duplicating
    setup or re-parsing results.
    """

    pipeline: PipelineSpec
    pipeline_context: PipelineContext
    nodes: dict[str, Any]
    steps: list[StepConfig]
    output_aliases: dict[str, str]
    text: str
    execution_id: str
    dag_executor: DAGExecutor
    recorder: EventRecorder
    start_monotonic: float = field(default=0.0)
