"""Refine generation context: scope-filter vocabulary + enrich must_include.

Runs after analyze_scope, before generate_rewrites/generate_hyde. Takes
predicted scopes and must_include from scope analysis, then:

1. Filters register vocabulary to predicted scopes only (~1500 → ~300 tokens)
2. Filters flat corpus hints to predicted scopes + co-occurrence
3. Enriches must_include with corpus-validated scope-specific anchors

No LLM call — pure data transformation using the property index
co-occurrence infrastructure.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, override

from systems.pipeline.core.events.step import RagGenerationContextRefined
from systems.pipeline.core.execution.resolver import NamespaceResolver
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from universal_logging import get_logger

from services.rag.corpus_hints import (
    filter_hints_by_cooccurrence,
    format_register_hints,
    get_hints_for_scopes,
    load_corpus_hints,
    load_scope_vocabulary,
)

if TYPE_CHECKING:
    from systems.pipeline.core.handlers.protocol import PipelineContext
    from systems.pipeline.core.schemas import StepConfig

logger = get_logger(__name__)


class RefineGenerationContextHandler(BaseHandler):
    """Scope-filter vocabulary and enrich must_include for generation steps."""

    step_type = "refine_generation_context_v1"

    @override
    async def execute(
        self,
        step: StepConfig,
        context: PipelineContext,
    ) -> StepOutput:
        resolver = NamespaceResolver(context)

        scopes = self._resolve_list_input(resolver, step, "scopes")
        must_include = self._resolve_list_input(resolver, step, "must_include")
        suggested_terms = self._resolve_list_input(resolver, step, "suggested_terms")

        max_anchors: int = step.get_domain_field("max_scope_anchors", 2)
        min_cooccurrence: int = step.get_domain_field("min_chunk_cooccurrence", 1)
        max_hints: int = step.get_domain_field("max_hints", 7)

        vocabulary = load_scope_vocabulary()
        register_text = format_register_hints(vocabulary, scopes=scopes)
        register_total = len(vocabulary)
        register_included = (
            sum(1 for s in scopes if s in vocabulary) if scopes else register_total
        )

        hints_map = load_corpus_hints()
        flat_text = get_hints_for_scopes(hints_map, scopes=scopes)
        flat_terms = [t.strip() for t in flat_text.split(",") if t.strip()]

        filtered_flat: list[str] = []
        if flat_terms and suggested_terms:
            filtered_flat = filter_hints_by_cooccurrence(
                suggested_terms,
                flat_terms,
                min_chunk_cooccurrence=min_cooccurrence,
            )
        if not filtered_flat:
            filtered_flat = flat_terms

        if max_hints > 0 and len(filtered_flat) > max_hints:
            filtered_flat = filtered_flat[:max_hints]

        scope_anchors = _select_scope_anchors(
            vocabulary,
            scopes,
            must_include,
            suggested_terms,
            max_anchors=max_anchors,
        )
        enriched = list(must_include) + scope_anchors

        parts = [
            ", ".join(filtered_flat) if filtered_flat else None,
            f"Vocabulary by register:\n{register_text}" if register_text else None,
        ]
        combined = "\n\n".join(filter(None, parts))

        logger.info(
            "Step '%s': scopes=%s, flat=%d→%d, register=%d/%d scopes, "
            "must_include=%s→%s (+%s)",
            step.id,
            scopes,
            len(flat_terms),
            len(filtered_flat),
            register_included,
            register_total,
            must_include,
            enriched,
            scope_anchors,
        )

        self._publish_bus_event(
            context,
            RagGenerationContextRefined(
                pipeline_id=context.pipeline.id,
                execution_id=context.execution_id,
                step_name=step.name,
                predicted_scopes=scopes,
                original_must_include=must_include,
                enriched_must_include=enriched,
                scope_anchors_added=scope_anchors,
                flat_hint_count=len(filtered_flat),
                register_scopes_included=register_included,
                register_scopes_total=register_total,
            ),
        )

        return StepOutput(
            raw=combined,
            json={
                "filtered_hints": combined,
                "enriched_must_include": enriched,
                "scope_anchors_added": scope_anchors,
                "original_must_include": must_include,
                "flat_hint_count": len(filtered_flat),
                "register_scopes_included": register_included,
            },
        )

    def _resolve_list_input(
        self,
        resolver: NamespaceResolver,
        step: StepConfig,
        key: str,
    ) -> list[str]:
        """Resolve a handler input expected to be a list of strings."""
        raw: list[Any] | str = self._resolve_input(
            resolver, step, key, step.handler_inputs
        )
        if isinstance(raw, list):
            return [str(x).strip() for x in raw if str(x).strip()]
        if isinstance(raw, str) and raw.strip():
            return [raw.strip()]
        return []

    @override
    def validate(self, step: StepConfig) -> list[str]:
        errors: list[str] = []
        for required in ("scopes", "must_include", "suggested_terms"):
            if not step.handler_inputs or required not in step.handler_inputs:
                errors.append(
                    f"Step '{step.id}' missing '{required}' in handler_inputs"
                )
        return errors


# Priority order for scope registers when selecting anchors; lower value = higher priority.
_REGISTER_PRIORITY: dict[str, int] = {
    "specification": 0,
    "practitioner": 1,
    "academic": 2,
}


def _select_scope_anchors(
    vocabulary: dict[str, dict[str, list[str]]],
    scopes: list[str],
    must_include: list[str],
    suggested_terms: list[str],
    *,
    max_anchors: int = 2,
) -> list[str]:
    """Select discriminative scope-specific anchor terms for must_include.

    Gathers terms from predicted scopes' vocabulary in register-priority
    order (specification → practitioner → academic), filters out terms
    already in must_include or query, validates via co-occurrence with
    suggested_terms, and returns the top candidates.

    ∀ scopes with len > 1: skip anchor injection — multi-scope predictions
    cover broader query intent where vocabulary spec-terms are less
    discriminative than the query-derived must_include terms from analyze_scope.
    Single-scope predictions have tight vocabulary alignment (e.g.,
    temporal_provenance → prov:wasderivedfrom) that genuinely adds signal.
    """
    if not scopes or not vocabulary or max_anchors <= 0:
        return []

    if len(scopes) > 1:
        return []

    existing_lower = {t.lower() for t in must_include}
    query_lower = {t.lower() for t in suggested_terms}

    candidates: list[str] = []
    for scope in scopes:
        scope_regs = vocabulary.get(scope, {})
        for register in sorted(scope_regs, key=lambda r: _REGISTER_PRIORITY.get(r, 99)):
            for term in scope_regs[register]:
                clean = term.rstrip("*").strip()
                if not clean:
                    continue
                cl = clean.lower()
                if cl in existing_lower or cl in query_lower:
                    continue
                candidates.append(clean)

    if not candidates:
        return []

    unique: list[str] = []
    seen: set[str] = set()
    for c in candidates:
        cl = c.lower()
        if cl not in seen:
            seen.add(cl)
            unique.append(c)

    validated = filter_hints_by_cooccurrence(
        suggested_terms,
        unique,
        min_chunk_cooccurrence=1,
    )

    if validated:
        # Co-occurrence is a threshold filter only; preserve register-priority order
        # from `unique` rather than using the co-occurrence-sorted result.
        validated_lower = {v.lower() for v in validated}
        priority_ordered = [c for c in unique if c.lower() in validated_lower]
        return priority_ordered[:max_anchors]

    return unique[:max_anchors]
