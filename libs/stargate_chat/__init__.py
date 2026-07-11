"""Stargate chat-completions transport — shared by ocr_core, finance, and other callers.

Public API:
    call_stargate(stargate_url, messages, *, model, system="", max_tokens=None) -> dict
        Raw POST to Stargate ``/v1/chat/completions``. Returns OpenAI-format JSON
        or ``{"error": "..."}`` on failure.

    extract_stargate_text(resp) -> str
        Pull assistant text from an OpenAI-format chat completion response.

``stargate_url`` is always an explicit first argument — callers resolve it once
(typically from ``STARGATE_URL`` env or ``transport_utils.DEFAULT_STARGATE_URL``)
and thread it through. This module owns transport, not URL resolution.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from transport_utils import make_sync_client
from universal_logging import get_logger

logger = get_logger(__name__)

_STARGATE_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=10.0)


def call_stargate(
    stargate_url: str,
    messages: list[dict[str, Any]],
    *,
    model: str,
    system: str = "",
    max_tokens: int | None = None,
) -> dict[str, Any]:
    """POST to Stargate /v1/chat/completions — returns raw OpenAI-format response.

    Uses ``transport_utils.make_sync_client`` so both UDS and TCP Stargate URLs
    work. Returns ``{"error": "..."}`` on transport/HTTP failure.
    """
    wire: list[dict[str, Any]] = []
    if system:
        wire.append({"role": "system", "content": system})
    wire.extend(messages)
    body: dict[str, Any] = {
        "model": model,
        "messages": wire,
        "stream": False,
    }
    if max_tokens is not None:
        body["max_tokens"] = max_tokens
    try:
        with make_sync_client(stargate_url, timeout=_STARGATE_TIMEOUT.read) as client:
            resp = client.post("/v1/chat/completions", json=body)
    except httpx.TimeoutException:
        return {"error": "Stargate timeout"}
    except httpx.RequestError as exc:
        logger.error("Stargate request failed: %s", exc)
        return {"error": "Stargate connection failed"}
    if resp.status_code >= 400:
        logger.warning("Stargate returned %d for model=%s", resp.status_code, model)
        return {
            "error": f"Upstream error ({resp.status_code})",
            "detail": resp.text[:500],
        }
    try:
        data = resp.json()
    except json.JSONDecodeError:
        return {"error": "Stargate returned invalid JSON"}
    if not isinstance(data, dict):
        return {"error": "Stargate returned non-object JSON"}
    return data


def extract_stargate_text(resp: dict[str, Any]) -> str:
    """Pull assistant text from an OpenAI-format chat completion response."""
    if "error" in resp:
        return f"[OCR error: {resp['error']}]"
    choices = resp.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    return message.get("content") or ""


__all__ = ["call_stargate", "extract_stargate_text"]
