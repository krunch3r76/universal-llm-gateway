"""Never-raise POST of a CloseoutMemoRequest to the closeout-memo pipeline."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx
from transport_utils import DEFAULT_STARGATE_URL, make_async_client

from closeout_memo.models import CloseoutMemoRequest

logger = logging.getLogger(__name__)

CAPABILITY_PATH = "/api/v1/capabilities/closeout_memo/closeout-memo"
_TIMEOUT_S = 15.0


@dataclass(frozen=True, slots=True)
class PostResult:
    """Producer-facing result. ``accepted`` is any HTTP 2xx."""

    accepted: bool
    status_code: int
    error: str | None = None


async def post_closeout_memo(
    request: CloseoutMemoRequest,
    *,
    base_url: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> PostResult:
    """POST the capability route. Transport and HTTP errors return ``accepted=False``."""
    payload = {
        "model": "closeout-memo",
        "messages": [{"role": "user", "content": "closeout-memo"}],
        "pipeline_options": request.model_dump(exclude={"rejected_fields"}),
    }
    url_base = (base_url or DEFAULT_STARGATE_URL).rstrip("/")

    async def _send(http: httpx.AsyncClient) -> PostResult:
        resp = await http.post(CAPABILITY_PATH, json=payload)
        if 200 <= resp.status_code < 300:
            return PostResult(accepted=True, status_code=resp.status_code)
        return PostResult(
            accepted=False,
            status_code=resp.status_code,
            error=(resp.text or "")[:300],
        )

    try:
        if client is not None:
            return await _send(client)
        async with make_async_client(url_base, timeout=_TIMEOUT_S) as http:
            return await _send(http)
    except Exception as exc:  # noqa: BLE001 — producer must not raise
        logger.warning("closeout memo post failed: %s", exc)
        return PostResult(accepted=False, status_code=0, error=type(exc).__name__)


def request_from_options(options: dict[str, Any]) -> CloseoutMemoRequest:
    """Build a request from pipeline options. Raises ValidationError on a bad body.

    YAML knobs such as ``timeout_seconds`` are ignored. A nested
    ``pipeline_options`` dict is preferred when present.
    """
    source = options.get("pipeline_options")
    if not isinstance(source, dict):
        source = options
    picked = {
        key: source[key]
        for key in CloseoutMemoRequest.model_fields
        if key in source and key != "rejected_fields"
    }
    return CloseoutMemoRequest.model_validate(picked)
