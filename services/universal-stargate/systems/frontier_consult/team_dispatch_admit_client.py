"""HTTP client for team-dispatch-admit pipeline (fail-closed)."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from model_id import ModelId, canonical_model_entity_id
from transport_utils import DEFAULT_STARGATE_URL, make_sync_client

from .admission import FrontierEndpointError
from .events import (
    FrontierAdmitDecided,
    FrontierAdmitRejected,
    FrontierAdmitUnavailable,
)

logger = logging.getLogger(__name__)

PIPELINE_ID = "team-dispatch-admit"
ADMIT_READ_TIMEOUT_S = 25.0

EventPublisher = Callable[[Any], None]


def _body_to_options(body: Any, *, request_id: str) -> dict[str, Any]:
    return {
        "op": getattr(body, "op", None),
        "role": getattr(body, "role", None),
        "seat": getattr(body, "seat", None),
        "model": getattr(body, "model", None),
        "job": getattr(body, "job", None),
        "dispatch_id": getattr(body, "dispatch_id", None),
        "request_id": request_id,
    }


def _parse_plan_content(content: str) -> tuple[dict[str, Any] | None, str | None]:
    text = (content or "").strip()
    if not text:
        return None, "malformed"
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None, "malformed"
    if not isinstance(data, dict):
        return None, "malformed"
    if data.get("admitted") is False:
        return data, None
    if data.get("admitted") is True and data.get("backend"):
        return data, None
    return None, "malformed"


def _model_entity_id(plan: dict[str, Any], fallback_model: str | None) -> str:
    model = plan.get("model") or fallback_model
    if model:
        try:
            return str(canonical_model_entity_id(ModelId.parse(str(model))))
        except (TypeError, ValueError):
            pass
    return ""


def _emit_admit_event(
    *,
    plan: dict[str, Any],
    request_id: str,
    execution_id: str,
    fallback_model: str | None,
    publisher: EventPublisher | None,
) -> None:
    if publisher is None:
        return
    mid = _model_entity_id(plan, fallback_model)
    if plan.get("admitted") is False:
        publisher(
            FrontierAdmitRejected(
                request_id=request_id,
                execution_id=execution_id,
                model_entity_id=mid,
                admitted=False,
                code=str(plan.get("code") or ""),
                field=str(plan.get("field") or ""),
                status_code=int(plan.get("status_code") or 422),
            )
        )
        return
    publisher(
        FrontierAdmitDecided(
            request_id=request_id,
            execution_id=execution_id,
            model_entity_id=mid,
            admitted=True,
            code="",
            field="",
            status_code=200,
        )
    )


def run_team_dispatch_admit_sync(
    *,
    body: Any,
    request_id: str,
    execution_id: str = "",
    stargate_url: str = DEFAULT_STARGATE_URL,
    timeout_s: float = ADMIT_READ_TIMEOUT_S,
    event_publisher: EventPublisher | None = None,
) -> dict[str, Any]:
    """POST pipeline; return AdmitPlan dict or raise FrontierEndpointError."""
    options = _body_to_options(body, request_id=request_id)
    payload = {
        "model": PIPELINE_ID,
        "messages": [{"role": "user", "content": "admit"}],
        "pipeline_options": options,
    }
    cause = "unreachable"
    try:
        with make_sync_client(stargate_url, timeout=timeout_s) as client:
            resp = client.post("/v1/chat/completions", json=payload)
            if resp.status_code >= 500 or resp.status_code == 503:
                cause = "unreachable"
                _emit_unavailable(
                    request_id=request_id,
                    execution_id=execution_id,
                    fallback_model=getattr(body, "model", None),
                    cause=cause,
                    publisher=event_publisher,
                )
                raise FrontierEndpointError(
                    request_id=request_id,
                    field="admit",
                    reason="team dispatch admit pipeline unavailable",
                    status_code=503,
                    code="team_dispatch_admit_unavailable",
                    details={"cause": cause},
                )
            resp.raise_for_status()
            data = resp.json()
            choices = data.get("choices") or []
            content = ""
            if choices:
                content = str((choices[0].get("message") or {}).get("content") or "")
    except FrontierEndpointError:
        raise
    except Exception as exc:
        logger.warning("team_dispatch_admit transport failed: %s", exc)
        if "timeout" in type(exc).__name__.lower() or "Timeout" in str(exc):
            cause = "idle_timeout"
        _emit_unavailable(
            request_id=request_id,
            execution_id=execution_id,
            fallback_model=getattr(body, "model", None),
            cause=cause,
            publisher=event_publisher,
        )
        raise FrontierEndpointError(
            request_id=request_id,
            field="admit",
            reason="team dispatch admit pipeline unavailable",
            status_code=503,
            code="team_dispatch_admit_unavailable",
            details={"cause": cause},
        ) from exc

    plan, mal = _parse_plan_content(content)
    if mal:
        _emit_unavailable(
            request_id=request_id,
            execution_id=execution_id,
            fallback_model=getattr(body, "model", None),
            cause="malformed",
            publisher=event_publisher,
        )
        raise FrontierEndpointError(
            request_id=request_id,
            field="admit",
            reason="team dispatch admit pipeline returned malformed output",
            status_code=503,
            code="team_dispatch_admit_unavailable",
            details={"cause": "malformed"},
        )

    assert plan is not None
    _emit_admit_event(
        plan=plan,
        request_id=request_id,
        execution_id=execution_id,
        fallback_model=getattr(body, "model", None),
        publisher=event_publisher,
    )
    if plan.get("admitted") is False:
        raise FrontierEndpointError(
            request_id=request_id,
            field=str(plan.get("field") or "admit"),
            reason=str(plan.get("reason") or "admitted"),
            status_code=int(plan.get("status_code") or 422),
            code=str(plan.get("code") or "admit_rejected"),
            details=plan.get("details") if isinstance(plan.get("details"), dict) else None,
        )
    return plan


def _emit_unavailable(
    *,
    request_id: str,
    execution_id: str,
    fallback_model: str | None,
    cause: str,
    publisher: EventPublisher | None,
) -> None:
    if publisher is None:
        return
    mid = ""
    if fallback_model:
        try:
            mid = str(canonical_model_entity_id(ModelId.parse(str(fallback_model))))
        except (TypeError, ValueError):
            mid = ""
    publisher(
        FrontierAdmitUnavailable(
            request_id=request_id,
            execution_id=execution_id,
            model_entity_id=mid,
            admitted=False,
            code="team_dispatch_admit_unavailable",
            field="admit",
            status_code=503,
            cause=cause,
        )
    )


async def run_team_dispatch_admit(
    *,
    body: Any,
    request_id: str,
    execution_id: str = "",
    stargate_url: str = DEFAULT_STARGATE_URL,
    timeout_s: float = ADMIT_READ_TIMEOUT_S,
    event_publisher: EventPublisher | None = None,
) -> dict[str, Any]:
    import asyncio

    return await asyncio.to_thread(
        run_team_dispatch_admit_sync,
        body=body,
        request_id=request_id,
        execution_id=execution_id,
        stargate_url=stargate_url,
        timeout_s=timeout_s,
        event_publisher=event_publisher,
    )
