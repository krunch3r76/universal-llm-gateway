"""Resolve model_requirements to concrete model IDs via POST /v1/models/select.

Called at step init time. Results are cached for the execution lifetime.
Falls back gracefully when the endpoint is unavailable.

Transport is delegated to transport_utils (DEFAULT_STARGATE_URL resolution:
STARGATE_UNIX_SOCKET, then STARGATE_URL, then localhost:STARGATE_PORT) — the
same idiom as ProxyClient.

∀ async callers: use `async_resolve_model_requirements` — it uses an async
client so the event loop is never blocked. The sync variant exists only for
non-async call sites (DAG pre-analysis, step config introspection).
"""

from __future__ import annotations

from time import monotonic
from typing import Any

import httpx
from transport_utils import DEFAULT_STARGATE_URL, make_async_client, make_sync_client
from universal_logging import get_logger

logger = get_logger(__name__)

# 15s was still too tight under concurrent pipeline load.
_DEFAULT_TIMEOUT = 45.0
_SELECT_PATH = "/v1/models/select"


async def async_resolve_model_requirements(
    requirements_dict: dict[str, Any],
    estimated_source_tokens: int | None = None,
) -> list[str]:
    """Resolve a model_requirements dict to a ranked list of model IDs.

    Non-blocking: uses httpx.AsyncClient so the event loop is never held.

    Args:
        requirements_dict: Raw dict from pipeline YAML (SelectionRequest shape).
        estimated_source_tokens: When set, activates large_payload_latency_bucket
            constraint if the count exceeds large_payload_threshold_tokens.
            Applied as a pre-call transform before forwarding to the endpoint.

    Returns:
        List of routable model IDs, ordered by suitability.
        Empty list if endpoint is unavailable or no matches found.
    """
    payload = _apply_payload_latency_constraint(
        dict(requirements_dict), estimated_source_tokens
    )
    started_at = monotonic()

    try:
        async with make_async_client(
            DEFAULT_STARGATE_URL, timeout=_DEFAULT_TIMEOUT
        ) as client:
            resp = await client.post(_SELECT_PATH, json=payload)
        resp.raise_for_status()
        data = resp.json()
        model_ids = [m["id"] for m in data.get("models", []) if isinstance(m, dict)]
        if not model_ids:
            logger.warning(
                "No models matched requirements: %s (path=%s)",
                requirements_dict,
                data.get("selection_path"),
            )
        else:
            logger.info(
                "Resolved requirements (task=%s path=%s): %s",
                requirements_dict.get("task"),
                data.get("selection_path"),
                model_ids,
            )
        return model_ids
    except httpx.TimeoutException as exc:
        logger.error(
            "model_requirements resolution timed out after %.2fs "
            "for task=%s payload=%s: %s",
            monotonic() - started_at,
            requirements_dict.get("task"),
            payload,
            exc,
        )
    except httpx.HTTPStatusError as exc:
        logger.error(
            "model_requirements resolution HTTP %d after %.2fs "
            "for task=%s payload=%s: %s",
            exc.response.status_code,
            monotonic() - started_at,
            requirements_dict.get("task"),
            payload,
            exc.response.text[:200],
        )
    except httpx.RequestError as exc:
        logger.error(
            "model_requirements resolution network error after %.2fs "
            "for task=%s payload=%s: %s",
            monotonic() - started_at,
            requirements_dict.get("task"),
            payload,
            exc,
        )
    except Exception as exc:
        logger.exception(
            "model_requirements resolution unexpected error after %.2fs "
            "for task=%s payload=%s: %s",
            monotonic() - started_at,
            requirements_dict.get("task"),
            payload,
            exc,
        )
    return []


def resolve_model_requirements(
    requirements_dict: dict[str, Any],
    estimated_source_tokens: int | None = None,
) -> list[str]:
    """Resolve a model_requirements dict to a ranked list of model IDs.

    Synchronous variant for non-async call sites (DAG pre-analysis, step config
    introspection). Blocks the calling thread for the duration of the HTTP call.

    ∀ async callers: use async_resolve_model_requirements instead — it is
    non-blocking and will not stall the event loop.

    Returns:
        List of routable model IDs, ordered by suitability.
        Empty list if endpoint is unavailable or no matches found.
    """
    payload = _apply_payload_latency_constraint(
        dict(requirements_dict), estimated_source_tokens
    )
    started_at = monotonic()

    try:
        with make_sync_client(DEFAULT_STARGATE_URL, timeout=_DEFAULT_TIMEOUT) as client:
            resp = client.post(_SELECT_PATH, json=payload)
        resp.raise_for_status()
        data = resp.json()
        model_ids = [m["id"] for m in data.get("models", []) if isinstance(m, dict)]
        if not model_ids:
            logger.warning(
                "No models matched requirements: %s (path=%s)",
                requirements_dict,
                data.get("selection_path"),
            )
        else:
            logger.info(
                "Resolved requirements (task=%s path=%s): %s",
                requirements_dict.get("task"),
                data.get("selection_path"),
                model_ids,
            )
        return model_ids
    except httpx.TimeoutException as exc:
        logger.error(
            "model_requirements resolution timed out after %.2fs "
            "for task=%s payload=%s: %s",
            monotonic() - started_at,
            requirements_dict.get("task"),
            payload,
            exc,
        )
    except httpx.HTTPStatusError as exc:
        logger.error(
            "model_requirements resolution HTTP %d after %.2fs "
            "for task=%s payload=%s: %s",
            exc.response.status_code,
            monotonic() - started_at,
            requirements_dict.get("task"),
            payload,
            exc.response.text[:200],
        )
    except httpx.RequestError as exc:
        logger.error(
            "model_requirements resolution network error after %.2fs "
            "for task=%s payload=%s: %s",
            monotonic() - started_at,
            requirements_dict.get("task"),
            payload,
            exc,
        )
    except Exception as exc:
        logger.exception(
            "model_requirements resolution unexpected error after %.2fs "
            "for task=%s payload=%s: %s",
            monotonic() - started_at,
            requirements_dict.get("task"),
            payload,
            exc,
        )
    return []


def _apply_payload_latency_constraint(
    payload: dict[str, Any],
    estimated_source_tokens: int | None,
) -> dict[str, Any]:
    """Inject max_latency_bucket from large_payload rule when threshold exceeded.

    Keeps this constraint in the pipeline layer — it's a pipeline concern
    (token estimation), not a selection concern. The endpoint receives the
    already-tightened max_latency_bucket and enforces it normally.
    """
    if estimated_source_tokens is None:
        return payload
    large_bucket = payload.get("large_payload_latency_bucket")
    threshold = payload.get("large_payload_threshold_tokens")
    if large_bucket is None or threshold is None:
        return payload
    if estimated_source_tokens <= threshold:
        return payload

    from intelligence_profiles.requirements import LATENCY_ORDER

    existing = payload.get("max_latency_bucket")
    existing_order = LATENCY_ORDER.get(existing, 99) if existing else 99
    if existing is None or LATENCY_ORDER.get(large_bucket, 99) < existing_order:
        logger.info(
            "Large payload (%d tokens > threshold %d): applying max_latency_bucket=%s",
            estimated_source_tokens,
            threshold,
            large_bucket,
        )
        payload = {**payload, "max_latency_bucket": large_bucket}
    return payload
