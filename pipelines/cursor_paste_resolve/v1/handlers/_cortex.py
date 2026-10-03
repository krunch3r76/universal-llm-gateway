"""Thin cortex-api /dispatch helper for cursor-paste-resolve."""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


async def cortex_dispatch(
    client: Any, tool: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    try:
        resp = await client.post(
            "/dispatch", json={"tool": tool, "arguments": arguments}
        )
    except Exception as exc:
        logger.warning("cortex dispatch %s failed: %s", tool, exc)
        return {"error": f"transport_error: {exc}"}
    if resp.status_code >= 400:
        try:
            body = resp.json()
            if isinstance(body, dict):
                return body if "error" in body else {"error": json.dumps(body)[:300]}
        except Exception:
            pass
        return {"error": f"http_{resp.status_code}: {resp.text[:300]}"}
    try:
        data = resp.json()
    except Exception as exc:
        return {"error": f"invalid_json_response: {exc}"}
    return data if isinstance(data, dict) else {"error": "non_object_response"}
