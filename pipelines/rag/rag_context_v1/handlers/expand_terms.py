"""Query factoring + IDF-weighted corpus expansion handler.

Wraps the pure functions in ``term_expansion`` as a pipeline step.
The handler form is used by the rewrite pipeline (separate step);
the direct pipeline computes facets inline via ``retrieval_execution``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, override

from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from universal_logging import get_logger

from .term_expansion import extract_content_words, extract_phrases, idf_expand

if TYPE_CHECKING:
    from systems.pipeline.core.handlers.protocol import PipelineContext
    from systems.pipeline.core.schemas import StepConfig

logger = get_logger(__name__)


# ── Handler ──────────────────────────────────────────────────────────────────


class ExpandTermsHandler(BaseHandler):
    """Query factoring + IDF corpus expansion for pool B sparse retrieval."""

    step_type = "rag_expand_terms_v1"

    @override
    async def execute(
        self,
        step: StepConfig,
        context: PipelineContext,
    ) -> StepOutput:
        max_phrases: int = step.get_domain_field("max_expansion_terms", 6)
        max_idf_terms: int = step.get_domain_field("max_idf_terms", 8)
        max_discriminative: int = step.get_domain_field("max_discriminative", 4)

        # ── Extract content words once, derive everything from them ──
        query_words = extract_content_words(context.source_text)
        query_word_set = frozenset(w.lower() for w in query_words)

        phrases = extract_phrases(context.source_text, max_phrases=max_phrases)

        emitted: set[str] = set(query_word_set)
        facets: list[dict[str, object]] = []
        for i, phrase in enumerate(phrases):
            terms: list[str] = [phrase]
            emitted.add(phrase.lower())
            for w in extract_content_words(phrase, min_len=3):
                wl = w.lower()
                terms.append(w)
                emitted.add(wl)
            facets.append({"label": f"query_facet_{i}", "terms": terms})

        idf_terms: list[str] = []
        if max_idf_terms > 0:
            raw_idf = idf_expand(
                query_words,
                max_discriminative=max_discriminative,
                max_results=max_idf_terms + len(emitted),
            )
            for t in raw_idf:
                if t.lower() not in emitted:
                    emitted.add(t.lower())
                    idf_terms.append(t)
                if len(idf_terms) >= max_idf_terms:
                    break
            if idf_terms:
                facets.append({"label": "corpus_expansion", "terms": idf_terms})

        logger.info(
            "Step '%s': %d phrase facets + %d IDF terms. Phrases: %s. IDF: %s",
            step.id,
            len(phrases),
            len(idf_terms),
            phrases,
            idf_terms[:6],
        )

        return StepOutput(
            raw=", ".join(phrases + idf_terms),
            json={
                "facets": facets,
                "phrases": phrases,
                "idf_expansion_terms": idf_terms,
            },
        )

    @override
    def validate(self, step: StepConfig) -> list[str]:
        return []
