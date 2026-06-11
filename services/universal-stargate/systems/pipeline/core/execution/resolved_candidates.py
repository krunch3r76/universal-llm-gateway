"""Per-execution cache for ranked model candidates."""

from __future__ import annotations

import json

from .requirements_resolver import async_resolve_model_requirements


def _cache_key(
    step_name: str,
    requirements: dict[str, object],
    estimated_source_tokens: int | None,
) -> str:
    return json.dumps(
        {
            "step_name": step_name,
            "requirements": requirements,
            "estimated_source_tokens": estimated_source_tokens,
        },
        sort_keys=True,
    )


async def get_ranked_candidates(
    *,
    context: object,
    step_name: str,
    requirements: dict[str, object],
    estimated_source_tokens: int | None = None,
) -> list[str]:
    """Resolve and cache ranked model IDs for one step within a pipeline execution.

    Stores results on ``context._resolved_model_candidates`` keyed by step name,
    requirements dict, and optional token estimate so repeated lookups within the
    same DAG run avoid duplicate routing calls.
    """
    cache: dict[str, list[str]] | None = getattr(
        context,
        "_resolved_model_candidates",
        None,
    )
    if cache is None:
        cache = {}
        setattr(context, "_resolved_model_candidates", cache)

    key = _cache_key(step_name, requirements, estimated_source_tokens)
    cached = cache.get(key)
    if cached is not None:
        return list(cached)

    resolved = await async_resolve_model_requirements(
        requirements,
        estimated_source_tokens=estimated_source_tokens,
    )
    cache[key] = list(resolved)
    return list(resolved)
