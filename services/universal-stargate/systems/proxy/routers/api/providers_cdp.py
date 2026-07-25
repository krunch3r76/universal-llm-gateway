"""Stargate-facing native CDP API — wraps Jupiter project-ask satellite.

Public peer of ``/api/v1/providers/{anthropic|xai|…}/…`` for the async CDP
substrate. Body SoT: ``cdp_ask.models.SubmitProjectAskRequest``.

Routes:
  POST /api/v1/providers/cdp/ask
  GET  /api/v1/providers/cdp/executions/{execution_id}
  POST /api/v1/providers/cdp/executions/{execution_id}/abort
"""

from __future__ import annotations

from typing import Any

from cdp_ask.client import relay_async
from cdp_ask.models import SubmitProjectAskRequest
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from universal_logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/providers/cdp", tags=["provider-native-cdp"])


def _as_response(
    status: int, payload: dict[str, Any] | bytes, media: str
) -> Response:
    if isinstance(payload, dict):
        return JSONResponse(status_code=status, content=payload)
    return Response(content=payload, status_code=status, media_type=media)


@router.post("/ask", status_code=202)
async def cdp_ask_submit(body: SubmitProjectAskRequest) -> Response:
    """Submit a CDP sealed ask (native CDP API).

    Proxies to satellite ``POST /v1/project-ask/executions``. Exposes the full
    harvest/output surface (``harvest_source``, ``expected_size``,
    ``download_output``, ``converse``, ``no_project_uuid``, …).
    """
    status, payload, media = await relay_async(
        "POST",
        "/v1/project-ask/executions",
        json_body=body.model_dump(exclude_none=True),
    )
    if status >= 400:
        logger.warning("CDP native ask submit failed: status=%s", status)
    return _as_response(status, payload, media)


@router.get("/executions/{execution_id}")
async def cdp_ask_poll(execution_id: str) -> Response:
    """Poll a CDP execution (native CDP API)."""
    status, payload, media = await relay_async(
        "GET",
        f"/v1/project-ask/executions/{execution_id}",
    )
    return _as_response(status, payload, media)


@router.post("/executions/{execution_id}/abort")
async def cdp_ask_abort(execution_id: str, request: Request) -> Response:
    """Abort a CDP execution (native CDP API)."""
    try:
        body = await request.json()
    except Exception:
        body = None
    json_body = body if isinstance(body, dict) else None
    status, payload, media = await relay_async(
        "POST",
        f"/v1/project-ask/executions/{execution_id}/abort",
        json_body=json_body,
    )
    return _as_response(status, payload, media)
