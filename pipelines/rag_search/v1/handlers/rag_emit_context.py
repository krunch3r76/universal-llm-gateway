"""Terminal rag-search output step.

``rag-search`` names this step as ``output``. A disabled or condition-skipped
rerank step records ``StepOutput(raw="")``. Copying that empty string made MCP
and ``{writing_context}`` empty even when retrieve had chunks. When rerank
completed, this step returns that step's raw text unchanged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, override

from systems.pipeline.core.execution.resolver import NamespaceResolver
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from .context_formatting import format_context, merge_adjacent_chunks

if TYPE_CHECKING:
    from systems.pipeline.core.handlers.protocol import PipelineContext
    from systems.pipeline.core.schemas import StepConfig


class RagEmitContextHandler(BaseHandler):
    """Emit formatted context from rerank, or from retrieve when rerank was skipped."""

    step_type: str = "rag_emit_context_v1"

    @override
    async def execute(
        self,
        step: StepConfig,
        context: PipelineContext,
    ) -> StepOutput:
        binding = (step.handler_inputs or {}).get("rerank_result")
        rerank_name = (
            binding.step_name if binding is not None and binding.step_name else "rerank"
        )
        rerank = context.get_output(rerank_name)
        rerank_json = (
            rerank.json if rerank is not None and isinstance(rerank.json, dict) else {}
        )
        if rerank is not None and not rerank_json.get("_skipped"):
            return StepOutput(raw=rerank.raw or "", json=rerank_json)

        chunks = self._chunks(step, context)
        effective = context.options
        text = format_context(
            merge_adjacent_chunks(chunks) if chunks else [],
            include_section_headings=bool(
                effective.get("rag_include_section_headings", False)
            ),
            include_source_titles=bool(
                effective.get("rag_include_source_titles", False)
            ),
        )
        status = "skipped_no_chunks" if not chunks else "disabled"
        return StepOutput(
            raw=text,
            json={"rerank_status": status, "chunks_emitted": len(chunks)},
        )

    def _chunks(
        self, step: StepConfig, context: PipelineContext
    ) -> list[dict[str, Any]]:
        try:
            resolved = self._resolve_input(
                NamespaceResolver(context), step, "chunks_data", step.handler_inputs
            )
        except KeyError:
            return []
        if not isinstance(resolved, list):
            return []
        return [row for row in resolved if isinstance(row, dict)]
