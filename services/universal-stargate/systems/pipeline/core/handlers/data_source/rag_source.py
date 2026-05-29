"""``rag_corpus_hints`` source runner for ``data_source_v1``.

Builds the per-scope corpus-hint payload consumed by downstream vocabulary
steps. For each configured RAG scope it loads the curated hint terms, computes
the current corpus files hash, and (when ``skip_fresh`` is set) drops scopes
whose hash is unchanged under the tier-aware freshness gate. The ``services.rag``
dependencies are imported lazily inside the runner — preserving the monolith's
import-time contract — and the ``PropertyIndex`` is started/stopped around the
scope loop in a try/finally. A failure to load RAG config returns a structured
``rag_config_unavailable`` payload rather than raising.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from universal_logging import get_logger

from .freshness import should_skip_fresh_scope

if TYPE_CHECKING:
    from ...schemas import StepConfig
    from ..protocol import PipelineContext

logger = get_logger(__name__)


async def run_rag_corpus_hints(
    step: StepConfig, context: PipelineContext
) -> dict[str, Any]:
    """Retrieves RAG corpus hints, optionally filtering by scope and freshness.

    Args:
        step: The `StepConfig` object (used for logging, not for inputs).
        context: The `PipelineContext` carrying options 'mode', 'skip_fresh',
            and 'scopes'.

    Returns:
        A dictionary containing 'scopes' (list of dictionaries with scope details),
        'mode', and 'skip_fresh' status. Includes an 'error' key if RAG config fails.
    """
    from services.rag.config import load_config
    from services.rag.corpus_hints import (
        compute_scope_files_hash,
        load_corpus_hints,
    )
    from services.rag.property_index import PropertyIndex

    opts = context.options
    mode = str(opts.get("mode") or "local").strip().lower()
    skip_fresh = opts.get("skip_fresh", True)
    if isinstance(skip_fresh, str):
        skip_fresh = skip_fresh.strip().lower() in ("1", "true", "yes")
    skip_fresh = bool(skip_fresh)

    filter_scopes = opts.get("scopes")
    filter_set: set[str] | None = None
    if filter_scopes is not None and isinstance(filter_scopes, list | tuple | set):
        filter_set = {str(s) for s in filter_scopes if s is not None and str(s)}

    hints_map = load_corpus_hints()
    try:
        config = load_config()
    except Exception as e:  # Catch a broader exception if other errors are possible
        logger.error(
            "[%s] rag_corpus_hints: load_config failed: %s",
            step.id,
            e,
            exc_info=True,
        )
        return {
            "scopes": [],
            "mode": mode,
            "skip_fresh": skip_fresh,
            "error": "rag_config_unavailable",
        }
    cs_map = {n: list(sdef.prefixes) for n, sdef in config.scopes.items()}
    descriptions = {
        n: getattr(sdef, "description", "") or "" for n, sdef in config.scopes.items()
    }

    idx = PropertyIndex()
    await idx.start()
    try:
        scopes_out: list[dict[str, Any]] = []
        skipped_empty_hint_scopes = 0
        for scope_name in sorted(cs_map.keys()):
            if filter_set is not None and scope_name not in filter_set:
                continue
            text = hints_map.get(scope_name, "")
            terms = [t.strip() for t in text.split(",") if t.strip()]
            current_hash = compute_scope_files_hash(idx, cs_map[scope_name])
            stored = idx.get_scope_freshness(scope_name)
            if should_skip_fresh_scope(
                skip_fresh=skip_fresh,
                mode=mode,
                current_hash=current_hash,
                stored=stored,
            ):
                continue
            if not terms:
                skipped_empty_hint_scopes += 1
                continue
            scopes_out.append(
                {
                    "scope": scope_name,
                    "description": descriptions.get(scope_name, ""),
                    "terms": terms,
                    "has_hints": bool(terms),
                    "files_hash": current_hash,
                }
            )
    finally:
        await idx.stop()

    if skipped_empty_hint_scopes:
        logger.info(
            "[%s] rag_corpus_hints: skipped %d scope(s) with no corpus hints",
            step.id,
            skipped_empty_hint_scopes,
        )

    return {
        "scopes": scopes_out,
        "mode": mode,
        "skip_fresh": skip_fresh,
    }
