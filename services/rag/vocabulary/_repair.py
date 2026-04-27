"""Orchestrator: run_scope_freshness_repair — refresh hints and vocabulary for stale scopes."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from services.rag.corpus_hints import load_corpus_hints, update_corpus_hints

from ._classify import _classify_frontier_scopes, _classify_scopes_via_pipeline
from ._scope_helpers import _resolve_scope_vocab_mode, configured_scopes_map
from ._stargate import DEFAULT_STARGATE_CHAT_URL

if TYPE_CHECKING:
    from universal_event_bus import EventBus

    from services.rag.config import RagConfig
    from services.rag.property_index import PropertyIndex

logger = logging.getLogger(__name__)


async def run_scope_freshness_repair(
    *,
    property_index: PropertyIndex,
    config: RagConfig,
    stale_scopes: list[str],
    event_bus: EventBus | None,
    trigger: str,
    chat_url: str = DEFAULT_STARGATE_CHAT_URL,
) -> None:
    """Refresh corpus hints and vocabulary for scopes whose file-set hash drifted.

    Per-scope vocab_mode (set in rag.yaml under each scope) overrides the global
    vocabulary_mode. Scopes with vocab_mode=frontier use vocab-classify-v1;
    scopes with vocab_mode=none are skipped (corpus hints still refresh);
    all others use the lightweight local-model path.

    Local classification requires vocabulary_model to be set in rag.yaml. If it
    is empty, local-mode scopes are skipped and rag.vocabulary.gaps.detected is
    emitted with reason="no_model_configured".
    """
    if not stale_scopes:
        return

    from services.rag.events.lifecycle import (
        rag_hints_gaps_repaired,
        rag_vocabulary_classification_failed,
        rag_vocabulary_gaps_detected,
        rag_vocabulary_gaps_repaired,
    )

    cs_map = configured_scopes_map(config)
    model = config.vocabulary_model

    hints_updated: list[str] = []
    vocab_ok: list[str] = []
    vocab_failed: list[str] = []
    vocab_failure_reasons: dict[str, str] = {}
    no_model_scopes: list[str] = []
    frontier_no_terms: list[str] = []

    for scope_name in stale_scopes:
        if scope_name not in cs_map:
            continue
        sdef = config.scopes.get(scope_name)  # non-None: cs_map guarantees presence
        if sdef is not None and sdef.is_union:
            continue
        await update_corpus_hints(
            property_index,
            scope=scope_name,
            configured_scopes=cs_map,
            event_bus=event_bus,
        )
        hints_updated.append(scope_name)

    # Only classify scopes that have no existing vocabulary candidates
    # and are not explicitly opting out of classification (vocab_mode=none).
    # Corpus hints are always refreshed above; classification is expensive
    # and runs once per scope — until vocabulary is explicitly cleared.
    classify_scopes = [
        s
        for s in set(stale_scopes) & set(cs_map.keys())
        if not property_index.has_scope_vocabulary(s)
        and _resolve_scope_vocab_mode(s, config) != "none"
    ]

    local_scopes = [
        s for s in classify_scopes if _resolve_scope_vocab_mode(s, config) == "local"
    ]
    frontier_scopes = [
        s for s in classify_scopes if _resolve_scope_vocab_mode(s, config) == "frontier"
    ]

    if not model and local_scopes:
        no_model_scopes.extend(local_scopes)
        logger.warning(
            "%d scope(s) need vocabulary classification but vocabulary_model is not "
            "configured — skipping: %s",
            len(local_scopes),
            local_scopes,
        )
    elif local_scopes:
        local_written = await _classify_scopes_via_pipeline(
            sorted(local_scopes), mode="local", chat_url=chat_url, model_id=model
        )
        vocab_ok.extend(s for s in local_scopes if s in local_written)
        local_failed_set = [s for s in local_scopes if s not in local_written]
        vocab_failed.extend(local_failed_set)
        for s in local_failed_set:
            vocab_failure_reasons[s] = "local_classification_failed"

    if frontier_scopes:
        hints_map_f = load_corpus_hints()
        classifiable_frontier: list[str] = []
        for scope_name in frontier_scopes:
            terms_f = [
                t.strip()
                for t in hints_map_f.get(scope_name, "").split(",")
                if t.strip()
            ]
            if not terms_f:
                frontier_no_terms.append(scope_name)
            elif not any(c.isascii() and c.isalpha() for term in terms_f for c in term):
                vocab_failed.append(scope_name)
                vocab_failure_reasons[scope_name] = "non_latin_terms"
            else:
                classifiable_frontier.append(scope_name)

        written: set[str] = set()
        if classifiable_frontier:
            written = await _classify_frontier_scopes(
                sorted(classifiable_frontier), chat_url=chat_url
            )
        vocab_ok.extend(s for s in classifiable_frontier if s in written)
        vocab_failed.extend(s for s in classifiable_frontier if s not in written)
        if written:
            logger.info(
                "Frontier vocab classification completed for %d scope(s): %s",
                len(written),
                sorted(written),
            )
        failed = sorted(set(classifiable_frontier) - written)
        if failed:
            for scope_name in failed:
                vocab_failure_reasons[scope_name] = "frontier_classification_failed"
            logger.warning(
                "Frontier vocab classification failed for %d scope(s): %s",
                len(failed),
                failed,
            )

    if vocab_ok or (hints_updated and not vocab_failed and not no_model_scopes):
        await property_index.stamp_watermark("vocabulary")

    if vocab_failed:
        logger.warning(
            "Per-scope classify failed for %d scope(s): %s",
            len(vocab_failed),
            ", ".join(vocab_failed),
        )
    if vocab_ok:
        logger.info(
            "Per-scope classify completed for %d scope(s)",
            len(vocab_ok),
        )

    if hints_updated and event_bus is not None:
        await event_bus.publish_nowait(
            rag_hints_gaps_repaired(scopes=hints_updated, trigger=trigger)
        )

    if no_model_scopes and event_bus is not None:
        await event_bus.publish_nowait(
            rag_vocabulary_gaps_detected(
                scopes=no_model_scopes,
                reason="no_model_configured",
            )
        )

    if frontier_no_terms and event_bus is not None:
        await event_bus.publish_nowait(
            rag_vocabulary_gaps_detected(
                scopes=frontier_no_terms,
                reason="no_terms",
            )
        )

    if vocab_ok and event_bus is not None:
        await event_bus.publish_nowait(
            rag_vocabulary_gaps_repaired(
                scopes=sorted(vocab_ok),
                model=model or "frontier",
            )
        )

    if vocab_failed and event_bus is not None:
        await event_bus.publish_nowait(
            rag_vocabulary_classification_failed(
                scopes=sorted(vocab_failed),
                model=model or "frontier",
                trigger=trigger,
                reasons={
                    scope: vocab_failure_reasons.get(scope, "unknown")
                    for scope in sorted(vocab_failed)
                },
            )
        )

    if hints_updated:
        await property_index.stamp_watermark("corpus_hints")

    if vocab_failed:
        logger.error(
            "Vocabulary classification failed for scopes: %s",
            ", ".join(vocab_failed),
        )
