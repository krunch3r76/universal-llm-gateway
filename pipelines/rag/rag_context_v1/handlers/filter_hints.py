"""Filter corpus hints by co-occurrence with query-derived terms.

Reads suggest_terms output, loads all corpus hints, queries the property
index for document-level co-occurrence, and returns only the hints that
share source documents with the query terms. Falls back to all hints when
no co-occurrence is found (conservative — avoids empty prompt).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, override

from systems.pipeline.core.events.step import RagHintsFiltered
from systems.pipeline.core.execution.resolver import NamespaceResolver
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from universal_logging import get_logger

from services.rag.corpus_hints import (
    filter_hints_by_cooccurrence,
    get_hints_for_scopes,
    load_corpus_hints,
)

if TYPE_CHECKING:
    from systems.pipeline.core.handlers.protocol import PipelineContext
    from systems.pipeline.core.schemas import StepConfig

logger = get_logger(__name__)

_DEFAULT_HINTS_PATH = Path.home() / ".rag" / "corpus_hints.yaml"


class FilterCorpusHintsHandler(BaseHandler):
    """Filter corpus hints to those co-occurring with query terms."""

    step_type = "filter_corpus_hints_v1"

    @override
    async def execute(
        self,
        step: StepConfig,
        context: PipelineContext,
    ) -> StepOutput:
        resolver = NamespaceResolver(context)
        suggested_terms: Any = self._resolve_input(
            resolver, step, "suggested_terms", step.handler_inputs
        )
        if not isinstance(suggested_terms, list):
            suggested_terms = []
        query_terms: list[str] = [
            str(t) for t in suggested_terms if isinstance(t, str) and t.strip()
        ]

        min_chunk_cooccurrence: int = step.get_domain_field(
            "min_chunk_cooccurrence", 2
        )
        max_hints: int = step.get_domain_field("max_hints", 7)

        hints_path = _resolve_hints_path()
        hints_map = load_corpus_hints(hints_path)
        all_hints_text = get_hints_for_scopes(hints_map, scopes=None)
        all_terms = [t.strip() for t in all_hints_text.split(",") if t.strip()]

        if not all_terms:
            self._emit_event(
                context, step, query_terms, all_terms, [],
                min_threshold=min_chunk_cooccurrence, cap_limit=max_hints,
            )
            return StepOutput(
                raw="",
                json={"filtered_hints": "", "original_count": 0, "filtered_count": 0},
            )

        if not query_terms:
            logger.info(
                "Step '%s': no query terms — falling back to all %d hints",
                step.id,
                len(all_terms),
            )
            self._emit_event(
                context, step, query_terms, all_terms, all_terms,
                fallback=True, min_threshold=min_chunk_cooccurrence,
                cap_limit=max_hints,
            )
            return StepOutput(
                raw=all_hints_text,
                json={
                    "filtered_hints": all_hints_text,
                    "original_count": len(all_terms),
                    "filtered_count": len(all_terms),
                    "fallback": True,
                },
            )

        filtered = filter_hints_by_cooccurrence(
            query_terms, all_terms,
            min_chunk_cooccurrence=min_chunk_cooccurrence,
        )

        if not filtered:
            logger.info(
                "Step '%s': no co-occurring hints for terms %s — falling back to all %d",
                step.id,
                query_terms[:5],
                len(all_terms),
            )
            self._emit_event(
                context, step, query_terms, all_terms, all_terms,
                fallback=True, min_threshold=min_chunk_cooccurrence,
                cap_limit=max_hints,
            )
            return StepOutput(
                raw=all_hints_text,
                json={
                    "filtered_hints": all_hints_text,
                    "original_count": len(all_terms),
                    "filtered_count": len(all_terms),
                    "fallback": True,
                },
            )

        capped = max_hints > 0 and len(filtered) > max_hints
        if capped:
            filtered = filtered[:max_hints]

        filtered_text = ", ".join(filtered)
        logger.info(
            "Step '%s': filtered %d → %d hints%s for query terms %s",
            step.id,
            len(all_terms),
            len(filtered),
            f" (capped to {max_hints})" if capped else "",
            query_terms[:5],
        )
        self._emit_event(
            context, step, query_terms, all_terms, filtered,
            min_threshold=min_chunk_cooccurrence, capped=capped,
            cap_limit=max_hints,
        )
        return StepOutput(
            raw=filtered_text,
            json={
                "filtered_hints": filtered_text,
                "original_count": len(all_terms),
                "filtered_count": len(filtered),
            },
        )

    def _emit_event(
        self,
        context: PipelineContext,
        step: StepConfig,
        query_terms: list[str],
        all_terms: list[str],
        filtered_terms: list[str],
        *,
        fallback: bool = False,
        min_threshold: int = 2,
        capped: bool = False,
        cap_limit: int = 0,
    ) -> None:
        self._publish_bus_event(
            context,
            RagHintsFiltered(
                pipeline_id=context.pipeline.id,
                execution_id=context.execution_id,
                step_name=step.name,
                query_terms=query_terms,
                original_hint_count=len(all_terms),
                filtered_hint_count=len(filtered_terms),
                filtered_hints=filtered_terms,
                fallback=fallback,
                scoring_mode="chunk_weighted",
                min_threshold=min_threshold,
                capped=capped,
                cap_limit=cap_limit,
            ),
        )

    @override
    def validate(self, step: StepConfig) -> list[str]:
        errors: list[str] = []
        if not step.handler_inputs or "suggested_terms" not in step.handler_inputs:
            errors.append(
                f"Step '{step.id}' missing 'suggested_terms' in handler_inputs"
            )
        return errors


def _resolve_hints_path() -> Path:
    """Resolve corpus hints YAML path from RAG config or default."""
    try:
        from services.rag.config import load_config

        config = load_config()
        path = getattr(config, "corpus_hints_path", None)
        if path:
            return path
    except Exception:
        pass
    return _DEFAULT_HINTS_PATH
