"""Option mapping for the MCP ``rag(op=search)`` relay.

``finalize_relay_pipeline_options`` and ``fold_legacy_enable_flags`` are a
copy of ``services/universal-stargate/systems/pipeline/core/step_controls.py``.
The MCP container does not import ``systems``. ``test_rag_relay_options_match.py``
pins the two copies to the same results.
"""

from __future__ import annotations

from typing import Any

LEGACY_ENABLE_FLAGS: dict[str, str] = {
    "hyde_enabled": "generate_hyde",
    "rerank_enabled": "rerank",
    "catalog_retry_enabled": "fetch_scope_catalog_retry",
}

RAG_CONTEXT_STEP_CONTROLS_ERROR = "step controls apply to rag-search only"

SEARCH_RELAY_PIPELINE = "rag-search"
DEFAULT_TOP_K = 20


def map_search_tool_options(
    *,
    scope_override: str | list[str] | None,
    prefixes: list[str] | None,
    top_k: int,
    hyde_enabled: bool | None = None,
    rerank_enabled: bool | None = None,
    catalog_retry_enabled: bool | None = None,
    step_overrides: dict[str, Any] | None = None,
    skip_steps: list[str] | None = None,
) -> dict[str, Any]:
    """Map ``rag(op=search)`` arguments onto Stargate ``pipeline_options``.

    ``scope`` is already normalized to ``scope_override``. ``top_k`` becomes
    ``rag_max_chunks`` only when it differs from the pipeline default (20).
    Passing ``scope`` or ``top_k`` as those raw keys is not this mapping.
    Legacy enable flags stay raw here; ``finalize_relay_pipeline_options``
    folds them once the relay target is ``rag-search``.
    """
    options: dict[str, Any] = {"include_retrieval_metadata": True}
    if scope_override is not None:
        options["scope_override"] = scope_override
    if prefixes is not None:
        options["rag_source_prefixes"] = prefixes
    if top_k != DEFAULT_TOP_K:
        options["rag_max_chunks"] = top_k
    if hyde_enabled is not None:
        options["hyde_enabled"] = hyde_enabled
    if rerank_enabled is not None:
        options["rerank_enabled"] = rerank_enabled
    if catalog_retry_enabled is not None:
        options["catalog_retry_enabled"] = catalog_retry_enabled
    if step_overrides is not None:
        options["step_overrides"] = step_overrides
    if skip_steps is not None:
        options["skip_steps"] = skip_steps
    return options


def finalize_relay_pipeline_options(
    relay_target: str,
    options: dict[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    """Prepare ``pipeline_options`` for a Stargate relay target.

    ``rag-context`` keeps ``hyde_enabled`` / ``rerank_enabled`` /
    ``catalog_retry_enabled`` as raw keys and rejects ``step_overrides`` or
    ``skip_steps``. ``rag-search`` folds legacy enable flags into
    ``step_overrides``.
    """
    if relay_target == "rag-context":
        if options.get("step_overrides") or options.get("skip_steps"):
            return None, RAG_CONTEXT_STEP_CONTROLS_ERROR
        return dict(options), None
    if relay_target == "rag-search":
        return fold_legacy_enable_flags(options), None
    return dict(options), None


def fold_legacy_enable_flags(options: dict[str, Any]) -> dict[str, Any]:
    """Map hyde/rerank/catalog_retry flags onto step_overrides and drop them."""
    folded = dict(options)
    overrides = dict(folded.get("step_overrides") or {})
    for flag, step_name in LEGACY_ENABLE_FLAGS.items():
        if flag not in folded:
            continue
        enabled = bool(folded.pop(flag))
        current = dict(overrides.get(step_name) or {})
        current["enabled"] = enabled
        overrides[step_name] = current
    if overrides:
        folded["step_overrides"] = overrides
    elif "step_overrides" in folded and not folded["step_overrides"]:
        folded.pop("step_overrides", None)
    return folded
