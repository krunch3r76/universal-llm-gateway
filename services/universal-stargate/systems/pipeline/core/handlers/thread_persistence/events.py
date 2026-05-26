"""Async cortex-api dispatch client over UDS for thread-persistence helpers.

Wraps ``transport_utils.make_async_client`` against the cortex-api
``/dispatch`` endpoint. Normalises HTTP failures (``RequestError``, 4xx /
5xx, non-JSON bodies) into a uniform ``{error, status_code, detail?}``
dict so callers in ``anchor.py`` / ``window.py`` / ``artifact.py`` (and
Phase 4 handlers) can branch on a single shape without try/except
scaffolding at every call site.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from transport_utils import DEFAULT_CORTEX_URL, make_async_client
from universal_logging import get_logger

logger = get_logger(__name__)

_REQUEST_TIMEOUT = 10.0


async def cx_async(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Relay asynchronously to cortex-api via UDS, normalising error shape."""
    try:
        async with make_async_client(
            DEFAULT_CORTEX_URL, timeout=_REQUEST_TIMEOUT
        ) as client:
            response = await client.post(
                "/dispatch",
                json={"tool": tool, "arguments": json.dumps(arguments)},
            )
    except httpx.RequestError as exc:
        logger.error("cortex-api async relay failed: %s — %s", tool, exc)
        return {
            "error": f"cortex-api connection failed: {exc}",
            "status_code": None,
        }

    if response.status_code >= 400:
        detail = response.text
        msg = f"cortex-api error: HTTP {response.status_code}"
        if detail:
            msg += f" — {detail}"
        result: dict[str, Any] = {
            "error": msg,
            "status_code": response.status_code,
        }
        if detail:
            result["detail"] = detail
        return result

    try:
        return response.json()
    except (ValueError, json.JSONDecodeError) as exc:
        logger.error("cortex-api parse failed: %s", exc)
        return {
            "error": f"Invalid JSON response: {exc}",
            "status_code": None,
        }
