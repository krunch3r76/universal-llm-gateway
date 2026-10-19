"""Admission gates split from route.py. Behavior unchanged."""

from __future__ import annotations

import asyncio
import time
import uuid
from functools import partial
from typing import Any

from fastapi.responses import JSONResponse
from implement_admission.source_ref import SourceRefError, parse_source_ref
from pydantic import BaseModel

from .closeout_reply import parse_closeout_payload, run_implement_closeout_pipeline
from .deploy_state_gate import require_deploy_state
from .pinned_deliverable_ingress import (
    PinnedDeliverableBody,
    write_pinned_deliverable_via_cortex,
)
from .route_routers import implement_router, logger
from .service import (
    FrontierEndpointError,
)
from .stargate_cortex_reader import StargateCortexReader

_CLOSEOUT_DEDUPE_TTL_S = 3600.0
_closeout_dedupe: dict[
    str, float
] = {}  # TODO(durable-dedupe): todo:wire-closeout-trigger-consumer


def _closeout_dedupe_peek(key: str) -> bool:
    """Return True iff key already seen within TTL (does not record)."""
    now = time.monotonic()
    for k in [
        k for k, ts in _closeout_dedupe.items() if now - ts > _CLOSEOUT_DEDUPE_TTL_S
    ]:
        _closeout_dedupe.pop(k, None)
    return key in _closeout_dedupe


def _closeout_dedupe_record(key: str) -> None:
    """Record idempotency key after closeout pipeline accepts (non-poisoning)."""
    _closeout_dedupe[key] = time.monotonic()


class ImplementCloseoutBody(BaseModel):
    """Dispatched implement closeout — triggers pipeline:implement-closeout."""

    model_config = {"extra": "forbid"}

    closeout: dict[str, Any]
    source_ref: str | None = None
    idempotency_key: str | None = None


@implement_router.post("/closeout", status_code=200, response_model=None)
async def implement_closeout(
    body: ImplementCloseoutBody,
) -> dict[str, Any] | JSONResponse:
    """Apply ImplementCloseout via pipeline:implement-closeout (Step 4)."""
    request_id = uuid.uuid4().hex[:12]
    payload = parse_closeout_payload(body.closeout)
    if payload is None:
        return JSONResponse(
            status_code=422,
            content=FrontierEndpointError(
                request_id=request_id,
                field="closeout",
                reason="closeout must be a valid ImplementCloseout object",
                status_code=422,
                code="closeout_invalid",
            ).to_dict(),
        )

    # Idempotency peek (producer path only; record after gate + pipeline pass).
    if body.idempotency_key and _closeout_dedupe_peek(body.idempotency_key):
        logger.info("closeout deduped: key=%s", body.idempotency_key)
        return {"ok": True, "deduped": True, "idempotency_key": body.idempotency_key}

    # Status presence (ImplementCloseout requires it; fail fast at ingress).
    if "status" not in payload:
        return JSONResponse(
            status_code=422,
            content=FrontierEndpointError(
                request_id=request_id,
                field="closeout",
                reason="closeout.status is required",
                status_code=422,
                code="closeout_invalid",
            ).to_dict(),
        )

    # source_ref resolvability — required on keyed (producer) path; reject sidecars.
    effective_source_ref = body.source_ref or payload.get("source_ref")
    if body.idempotency_key:
        if effective_source_ref is None:
            logger.warning("closeout source_ref missing: key=%s", body.idempotency_key)
            return JSONResponse(
                status_code=422,
                content=FrontierEndpointError(
                    request_id=request_id,
                    field="source_ref",
                    reason="source_ref is required on the keyed (producer) path",
                    status_code=422,
                    code="closeout_source_unresolvable",
                ).to_dict(),
            )
        try:
            parse_source_ref(effective_source_ref)
        except SourceRefError as exc:
            logger.warning(
                "closeout source_ref unresolvable: key=%s ref=%s err=%s",
                body.idempotency_key,
                effective_source_ref,
                exc,
            )
            return JSONResponse(
                status_code=422,
                content=FrontierEndpointError(
                    request_id=request_id,
                    field="source_ref",
                    reason=f"source_ref not adapter-resolvable: {effective_source_ref}",
                    status_code=422,
                    code="closeout_source_unresolvable",
                ).to_dict(),
            )

    loop = asyncio.get_running_loop()
    admin_override = bool(payload.get("deploy_state_admin_override"))
    try:
        await loop.run_in_executor(
            None,
            partial(
                require_deploy_state,
                request_id=request_id,
                source_ref=effective_source_ref,
                closeout=payload,
                cortex=StargateCortexReader(),
                admin_override=admin_override,
            ),
        )
    except FrontierEndpointError as exc:
        logger.warning(
            "closeout deploy_state_gate reject: key=%s ref=%s code=%s",
            body.idempotency_key,
            effective_source_ref,
            exc.code,
        )
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    logger.info(
        "closeout accepted: key=%s ref=%s",
        body.idempotency_key,
        effective_source_ref,
    )
    try:
        result = await loop.run_in_executor(
            None,
            lambda: run_implement_closeout_pipeline(
                payload, source_ref=body.source_ref
            ),
        )
    except Exception as exc:
        logger.warning("implement closeout failed: request_id=%s %s", request_id, exc)
        return JSONResponse(
            status_code=502,
            content=FrontierEndpointError(
                request_id=request_id,
                field="closeout",
                reason=str(exc),
                status_code=502,
                code="closeout_pipeline_error",
            ).to_dict(),
        )
    if not result.get("ok", True) and result.get("error"):
        return JSONResponse(status_code=502, content=result)
    if body.idempotency_key:
        _closeout_dedupe_record(body.idempotency_key)
    return result


@implement_router.post("/pinned-deliverable", status_code=200, response_model=None)
async def implement_pinned_deliverable(
    body: PinnedDeliverableBody,
) -> dict[str, Any] | JSONResponse:
    """Write a packet-pinned deliverable to cortex via cortex-api (friction 19916)."""
    result = await write_pinned_deliverable_via_cortex(body)
    if "error" in result:
        status = result.get("status_code") or 502
        if isinstance(status, int) and status >= 400:
            return JSONResponse(status_code=status, content=result)
        return JSONResponse(status_code=502, content=result)
    return result
