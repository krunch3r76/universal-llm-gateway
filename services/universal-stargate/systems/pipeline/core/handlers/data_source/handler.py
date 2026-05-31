"""``DataSourceV1Handler`` — the ``data_source_v1`` step handler shell.

Thin class shell for the ``data_source_v1`` step type: owns the registry
decorator, the step type, the empty dependency-field tuple, and the valid
``source_type`` set, plus ``validate`` and a delegating ``execute`` that routes
to one of the three source runners by ``source_type`` and assembles the
``StepOutput``. All source logic lives in the package's free-function submodules
(``sqlite_source``, ``rag_source``, ``models_source``) per the class-delegator
pattern shared with ``generate/``, ``assess_loop/``, and ``frontier_dispatch/``.

``DataSourceV1Handler`` is intentionally standalone — it does NOT extend
``BaseHandler``.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, ClassVar

from ..protocol import StepOutput
from ..registry import register_handler
from .models_source import run_available_models
from .rag_source import run_rag_corpus_hints
from .sqlite_source import run_sqlite

if TYPE_CHECKING:
    from ...schemas import StepConfig
    from ..protocol import PipelineContext


@register_handler
class DataSourceV1Handler:
    """Load structured data for downstream steps (SQLite or RAG corpus_hints)."""

    step_type = "data_source_v1"

    dependency_fields: ClassVar[tuple[str, ...]] = ()

    _VALID_SOURCE_TYPES = frozenset(
        {"sqlite_query", "rag_corpus_hints", "available_models"}
    )

    def validate(self, step: StepConfig) -> list[str]:
        st = step.get_domain_field("source_type", "")
        if not st:
            return [f"Step '{step.id}': data_source_v1 requires source_type"]
        if st not in self._VALID_SOURCE_TYPES:
            return [
                f"Step '{step.id}': unknown source_type {st!r} "
                f"(expected {' | '.join(sorted(self._VALID_SOURCE_TYPES))})"
            ]
        return []

    async def execute(self, step: StepConfig, context: PipelineContext) -> StepOutput:
        source_type = self._require_source_type(step)
        if source_type == "sqlite_query":
            payload = await run_sqlite(step, context)
        elif source_type == "available_models":
            payload = await run_available_models(step, context)
        else:
            payload = await run_rag_corpus_hints(step, context)
        raw = json.dumps(payload, ensure_ascii=False, indent=2)
        return StepOutput(raw=raw, json=payload)

    def _require_source_type(self, step: StepConfig) -> str:
        """Extracts and validates the 'source_type' from the step configuration.

        Args:
            step: The `StepConfig` object.

        Returns:
            The validated source type string.

        Raises:
            ValueError: If 'source_type' is missing or empty.
        """
        st = step.get_domain_field("source_type", "")
        if not st:
            raise ValueError(f"Step '{step.id}': data_source_v1 requires source_type")
        return st
