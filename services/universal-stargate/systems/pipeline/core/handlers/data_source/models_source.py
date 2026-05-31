"""``available_models`` source runner for ``data_source_v1``.

Resolves the model pool a downstream step may route over. A caller-supplied
``model_pool`` list short-circuits discovery and is returned with ``mode:
"override"``. Otherwise it queries the gateway's ``GET /v1/models?type=model``
endpoint (adding ``source=all`` in ``frontier`` mode), parses and de-duplicates
the returned ids by routing key, and — in frontier mode — appends any extra
``frontier_models`` not already present. An empty resulting pool is an error.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
from model_id import ModelId
from universal_logging import get_logger

if TYPE_CHECKING:
    from ...schemas import StepConfig
    from ..protocol import PipelineContext

logger = get_logger(__name__)


async def run_available_models(
    step: StepConfig, context: PipelineContext
) -> dict[str, Any]:
    """GET /v1/models?type=model (& source=all iff mode=frontier).

    Options: model_pool (override), mode local|frontier, frontier_models.

    Returns: {"model_pool": [routing_key, ...], "mode": str}
    """
    opts = context.options
    override = opts.get("model_pool")
    if isinstance(override, list) and override:
        pool = [ModelId.parse(str(x).strip()) for x in override if str(x).strip()]
        if not pool:
            raise ValueError("model_pool override is empty after parsing")
        return {
            "model_pool": [m.routing_key for m in pool],
            "mode": "override",
        }

    mode = str(opts.get("mode") or "local").strip().lower()
    source = "all" if mode == "frontier" else None

    try:
        params: dict[str, str] = {"type": "model"}
        if source:
            params["source"] = source
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                "http://localhost:9999/v1/models",
                params=params,
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as e:
        logger.error(
            "[%s] available_models: GET /v1/models failed with status %s",
            step.id,
            e.response.status_code,
            exc_info=True,
        )
        raise ValueError(
            f"Model discovery failed for step '{step.id}': HTTP status error"
        ) from e
    except httpx.RequestError as e:
        logger.error(
            "[%s] available_models: GET /v1/models failed with request error",
            step.id,
            exc_info=True,
        )
        raise ValueError(
            f"Model discovery failed for step '{step.id}': Request error"
        ) from e
    except Exception as e:  # Catch any other unexpected errors
        logger.error(
            "[%s] available_models: GET /v1/models failed with unexpected error",
            step.id,
            exc_info=True,
        )
        raise ValueError(
            f"Model discovery failed for step '{step.id}': Unexpected error"
        ) from e

    models: list[ModelId] = []
    seen_models: set[ModelId] = set()
    for entry in data.get("data", []):
        mid_str = entry.get("id", "")
        if not mid_str.strip():
            continue
        model = ModelId.parse(mid_str.strip())
        if model not in seen_models:
            models.append(model)
            seen_models.add(model)

    pool_ids: list[str] = [m.routing_key for m in models]

    if mode == "frontier":
        extra = opts.get("frontier_models") or []
        if isinstance(extra, list):
            for x in extra:
                s = str(x).strip()
                if s and s not in pool_ids:
                    pool_ids.append(s)

    if not pool_ids:
        raise ValueError(
            f"Step '{step.id}': available_models found no models "
            "(load gateway models or pass model_pool / frontier_models)"
        )

    return {"model_pool": pool_ids, "mode": mode}
