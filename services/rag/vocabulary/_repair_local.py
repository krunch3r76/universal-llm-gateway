"""Local-model vocabulary classification loop for scope freshness repair."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import httpx

from services.rag.corpus_hints import compute_scope_files_hash, load_corpus_hints

from ._classify import classify_scope_async
from ._scope_helpers import _resolve_scope_vocab_mode

if TYPE_CHECKING:
    from services.rag.config import RagConfig
    from services.rag.property_index import PropertyIndex

logger = logging.getLogger(__name__)


async def _classify_local_scopes(
    client: httpx.AsyncClient,
    model: str,
    scopes: list[str],
    config: RagConfig,
    property_index: PropertyIndex,
    cs_map: dict[str, list[str]],
    chat_url: str,
) -> tuple[list[str], list[str], dict[str, str]]:
    """Classify a list of scopes using the local Stargate model.

    Returns (vocab_ok, vocab_failed, vocab_failure_reasons).
    Corpus hints and descriptions are loaded internally.
    """
    hints_map = load_corpus_hints()
    descriptions = {
        n: getattr(sdef, "description", "") or ""
        for n, sdef in config.scopes.items()
    }

    vocab_ok: list[str] = []
    vocab_failed: list[str] = []
    vocab_failure_reasons: dict[str, str] = {}

    for scope_name in sorted(scopes):
        text = hints_map.get(scope_name, "")
        terms = [t.strip() for t in text.split(",") if t.strip()]
        if not terms:
            vocab_failed.append(scope_name)
            vocab_failure_reasons[scope_name] = "no_terms"
            continue
        if not any(c.isascii() and c.isalpha() for term in terms for c in term):
            vocab_failed.append(scope_name)
            vocab_failure_reasons[scope_name] = "non_latin_terms"
            continue
        desc = descriptions.get(scope_name, "")
        try:
            result = await classify_scope_async(
                scope=scope_name,
                description=desc,
                terms=terms,
                model=model,
                taxonomy=config.vocabulary_taxonomy,
                chat_url=chat_url,
                client=client,
            )
        except Exception as e:
            logger.warning("Per-scope classify failed for %s: %s", scope_name, e)
            result = None

        if result is None:
            vocab_failed.append(scope_name)
            vocab_failure_reasons[scope_name] = "local_classification_failed"
            continue

        await property_index.replace_scope_vocabulary_for_scopes({scope_name: result})
        fh = compute_scope_files_hash(property_index, cs_map[scope_name])
        await property_index.store_scope_freshness(
            scope_name, fh, classified_tier="local"
        )
        vocab_ok.append(scope_name)

    return vocab_ok, vocab_failed, vocab_failure_reasons
