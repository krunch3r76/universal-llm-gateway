"""Context budget allocation for model-aware prompt assembly.

Resolves each target model's context window and computes how much room
is available for RAG findings and model output, so that both ``consult``
and ``ask`` adapt automatically to local 32K models and frontier 128K+
models without manual flag tuning.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx
from model_id import ModelId

logger = logging.getLogger(__name__)

CHARS_PER_TOKEN = 4
RESPONSE_RESERVE = 0.20
AVG_RAG_CHUNK_CHARS = 1200
DEFAULT_LOCAL_CONTEXT = 32_768
DEFAULT_FRONTIER_CONTEXT = 128_000
MIN_RAG_TOP_K = 1
MAX_RAG_TOP_K = 30


@dataclass(frozen=True, slots=True)
class ContextBudget:
    """Computed allocation for a single consultation request."""

    context_length_tokens: int
    usable_chars: int
    fixed_chars: int
    output_limit_chars: int
    rag_budget_chars: int
    adaptive_top_k: int


def resolve_context_length(
    model_id: str,
    *,
    stargate_url: str = "http://localhost:9999",
    timeout: float = 5.0,
) -> int:
    """Best-effort context length resolution for *model_id*.

    Resolution chain:
    1. ``ModelId.parse()`` — works when context is in the ID suffix
    2. ``/v1/models`` query — authoritative for registered models
    3. Heuristic default — 128K for ``/``-prefixed (frontier), 32K otherwise
    """
    parsed = ModelId.parse(model_id)
    if parsed.context_length is not None:
        return parsed.context_length

    ctx = _query_models_endpoint(model_id, stargate_url, timeout)
    if ctx is not None:
        return ctx

    if "/" in model_id:
        logger.info(
            "No context_length for frontier model %s — defaulting to %d",
            model_id,
            DEFAULT_FRONTIER_CONTEXT,
        )
        return DEFAULT_FRONTIER_CONTEXT

    logger.info(
        "No context_length for model %s — defaulting to %d",
        model_id,
        DEFAULT_LOCAL_CONTEXT,
    )
    return DEFAULT_LOCAL_CONTEXT


def resolve_min_context_length(
    model_ids: list[str],
    *,
    stargate_url: str = "http://localhost:9999",
    timeout: float = 5.0,
) -> int:
    """Return the smallest context length across *model_ids*.

    The budget is constrained by the most limited model so the assembled
    prompt fits every target.
    """
    if not model_ids:
        return DEFAULT_LOCAL_CONTEXT
    lengths = [
        resolve_context_length(mid, stargate_url=stargate_url, timeout=timeout)
        for mid in model_ids
    ]
    return min(lengths)


def compute_budget(
    context_length: int,
    fixed_chars: int,
    output_chars: int,
    *,
    top_k_cap: int = MAX_RAG_TOP_K,
) -> ContextBudget:
    """Allocate context window across output and RAG findings.

    Steps:
    1. Reserve ``RESPONSE_RESERVE`` of the window for the model's reply.
    2. Subtract *fixed_chars* (system prompt, headers, instructions).
    3. Fit the model output — truncate only if it would crowd out all RAG.
    4. Fill remaining space with RAG chunks up to *top_k_cap*.
    """
    usable_chars = int(context_length * CHARS_PER_TOKEN * (1 - RESPONSE_RESERVE))
    remaining = max(usable_chars - fixed_chars, 0)

    if top_k_cap <= 0:
        output_limit = min(output_chars, remaining)
        rag_budget = 0
        adaptive_top_k = 0
    else:
        min_rag_room = AVG_RAG_CHUNK_CHARS * MIN_RAG_TOP_K
        if output_chars > remaining - min_rag_room:
            output_limit = max(remaining - min_rag_room, remaining // 2, 0)
        else:
            output_limit = output_chars

        rag_budget = max(remaining - output_limit, 0)
        adaptive_top_k = max(
            MIN_RAG_TOP_K,
            min(rag_budget // AVG_RAG_CHUNK_CHARS, top_k_cap),
        )

    return ContextBudget(
        context_length_tokens=context_length,
        usable_chars=usable_chars,
        fixed_chars=fixed_chars,
        output_limit_chars=output_limit,
        rag_budget_chars=rag_budget,
        adaptive_top_k=adaptive_top_k,
    )


def _query_models_endpoint(
    model_id: str,
    stargate_url: str,
    timeout: float,
) -> int | None:
    """Try to get context_length from Stargate ``/v1/models``."""
    url = f"{stargate_url.rstrip('/')}/v1/models"
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(url)
        resp.raise_for_status()
        data = resp.json()
    except Exception:  # noqa: BLE001
        return None

    for entry in data.get("data", []):
        if entry.get("id") == model_id:
            ctx = entry.get("context_length")
            if isinstance(ctx, int) and ctx > 0:
                return ctx
    return None
