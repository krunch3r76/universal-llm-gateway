"""Block-await skill-suggest dispatch — worker-hop primary, direct suggest fallback."""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from typing import Any, Literal

import httpx
from cortex_store.routes._skill_index import index_envelope_fields
from cortex_store.routes._skill_suggest import (
    _humanize_slug,
    _is_loaded,
    build_loaded_set,
    norm_loaded,
    slug_from_source_uri,
)
from cortex_store.seat_applicability import canonical_seat_or_422
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError
from transport_utils import DEFAULT_CORTEX_URL, make_async_client
from universal_logging import get_logger

from .admission import FrontierEndpointError
from .skill_suggest_dispatch_closeout import (
    fetch_worker_closeout_body,
    load_ledger_snapshot,
    map_wait_outcome_to_degraded_reason,
)
from .skill_suggest_dispatch_config import (
    SkillSuggestDispatchConfig,
    load_skill_suggest_dispatch_config,
)
from .skill_suggest_durable_state import (
    find_durable_terminal_event,
    read_ledger_dispatch_row,
)
from .skill_suggest_worker_waiter import await_worker_completion
from .cursor_sdk_generate import dispatch_cursor_sdk_generate
from .cursor_sdk_worker_dispatch import worker_base_url
from .events import (
    FrontierSkillSuggestDispatchCompleted,
    FrontierSkillSuggestDispatchDegraded,
)
from .handoff import _workspaces_root
from .skill_suggest_dispatch_helpers import (
    build_worker_message,
    parse_envelope_from_closeout,
)

logger = get_logger(__name__)

skills_router = APIRouter(prefix="/api/v1/skills", tags=["skills"])

_CONTEXT_MAX = 16384
_BACKSTOP_SCORE = 10.0


class SkillSuggestDispatchRequest(BaseModel):
    model_config = {"extra": "forbid"}

    agent: str
    loaded: list[str]
    entity_ids: list[str] | None = None
    conversation_context: str | None = None
    limit: int = Field(default=8, ge=1, le=25)
    prefer_worker: bool = Field(default=True)


class SkillSuggestDispatchResponse(BaseModel):
    agent: str
    suggestions: list[dict[str, Any]]
    count: int
    omitted: list[Any]
    degraded_skills: list[Any]
    loaded_echo: list[Any]
    seat_preloaded: list[Any]
    ranker_status: str
    degraded: bool
    degraded_reason: str | None = None
    route: Literal["worker", "fallback"]
    dispatch_execution_id: str | None = None
    dispatch_durable: bool | None = None


def _publish_event(event: Any) -> None:
    try:
        from systems.proxy.dependencies import get_proxy

        proxy = get_proxy()
        event_bus = getattr(proxy, "event_bus", None)
        if event_bus is not None:
            event_bus.publish_from_sync(event)
    except Exception:
        return


def _agent_bus_headers() -> dict[str, str]:
    token = os.getenv("AGENT_BUS_TOKEN", "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


def _validate_loaded(loaded: Any) -> None:
    if not isinstance(loaded, list):
        raise HTTPException(
            status_code=422,
            detail={"code": "loaded_invalid", "message": "loaded must be a list"},
        )
    for item in loaded:
        if not isinstance(item, str):
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "loaded_invalid",
                    "message": "loaded elements must be strings",
                },
            )


def _canonicalize_agent(agent: str) -> str:
    if not agent or not str(agent).strip():
        raise HTTPException(
            status_code=422,
            detail={"code": "agent_required", "message": "agent is required"},
        )
    return canonical_seat_or_422(str(agent).strip())


def _dispatch_config() -> SkillSuggestDispatchConfig:
    return load_skill_suggest_dispatch_config()


def _publish_degraded_event(
    *,
    request_id: str,
    agent: str,
    route: str,
    reason: str,
    latency_ms: int,
    execution_id: str | None = None,
    thread_id: str | None = None,
    dispatch_id: str | None = None,
    last_worker_status: str | None = None,
    last_heartbeat_at: str | None = None,
) -> None:
    _publish_event(
        FrontierSkillSuggestDispatchDegraded(
            request_id=request_id,
            agent=agent,
            route=route,
            reason=reason,
            latency_ms=latency_ms,
            execution_id=execution_id,
            thread_id=thread_id,
            dispatch_id=dispatch_id,
            last_worker_status=last_worker_status,
            last_heartbeat_at=last_heartbeat_at,
        )
    )


async def await_worker_ack(
    *,
    thread_id: str,
    execution_id: str,
    dispatch_id: str | None,
    config: SkillSuggestDispatchConfig | None = None,
) -> bool:
    """True when ledger/lifecycle shows worker liveness; fail-fast on unreachable."""
    cfg = config or _dispatch_config()
    deadline = time.monotonic() + cfg.ack_window_seconds
    probe_url = f"{worker_base_url()}/api/v1/git/admin/dispatch-status"
    poll_s = min(cfg.idle_poll_interval_seconds, cfg.ack_window_seconds)
    async with make_async_client(
        worker_base_url(), timeout=cfg.worker_probe_timeout_seconds
    ) as client:
        while time.monotonic() < deadline:
            ledger = read_ledger_dispatch_row(
                dispatch_id=dispatch_id,
                execution_id=execution_id,
                thread_id=thread_id,
            )
            if ledger is not None and ledger.status in {
                "admitted",
                "running",
                "completed",
                "failed",
            }:
                return True
            if find_durable_terminal_event(
                execution_id=execution_id,
                thread_id=thread_id,
                dispatch_id=dispatch_id,
            ):
                return True
            try:
                resp = await client.get(probe_url, params={"thread_id": thread_id})
            except httpx.HTTPError as exc:
                logger.warning(
                    "skill_suggest_dispatch ack probe transport error: %s", exc
                )
                if ledger is None:
                    return False
                await asyncio.sleep(poll_s)
                continue
            if resp.status_code != 200:
                logger.warning(
                    "skill_suggest_dispatch ack probe rejected: status=%s body=%s",
                    resp.status_code,
                    resp.text[:200],
                )
                if ledger is None:
                    return False
                await asyncio.sleep(poll_s)
                continue
            status = resp.json().get("status")
            if status in {"admitted", "running", "completed", "failed"}:
                return True
            await asyncio.sleep(poll_s)
    return False


def _skill_source_uri(entity: dict[str, Any]) -> str | None:
    top = entity.get("source_uri")
    if top and str(top).strip():
        return str(top).strip()
    attrs = entity.get("attributes") or {}
    if isinstance(attrs, dict):
        raw = attrs.get("source_uri")
        if raw and str(raw).strip():
            return str(raw).strip()
    return None


def _entity_required_skills(entity: dict[str, Any]) -> list[str]:
    attrs = entity.get("attributes") or {}
    if not isinstance(attrs, dict):
        return []
    raw = attrs.get("required_skills")
    if not isinstance(raw, list):
        return []
    return [str(s).strip() for s in raw if str(s).strip()]


async def _cortex_entity_get(
    client: httpx.AsyncClient, entity_id: str
) -> dict[str, Any] | None:
    try:
        resp = await client.post(
            "/dispatch",
            json={"tool": "entity_get", "arguments": {"entity_id": entity_id}},
        )
    except httpx.HTTPError as exc:
        logger.warning(
            "skill_suggest backstop entity_get transport error id=%s error=%s",
            entity_id,
            exc,
        )
        return None
    if resp.status_code >= 400:
        logger.warning(
            "skill_suggest backstop entity_get rejected id=%s status=%s body=%s",
            entity_id,
            resp.status_code,
            resp.text[:200],
        )
        return None
    data = resp.json()
    return data if isinstance(data, dict) else None


async def _collect_required_skill_slugs(
    entity_ids: list[str], client: httpx.AsyncClient
) -> list[str]:
    slugs: list[str] = []
    for entity_id in entity_ids:
        entity = await _cortex_entity_get(client, entity_id)
        if entity is None:
            continue
        for slug in _entity_required_skills(entity):
            if slug not in slugs:
                slugs.append(slug)
    return slugs


def _backstop_suggestion(
    *,
    skill_entity: dict[str, Any],
    slug: str,
    source_uri: str,
) -> dict[str, Any]:
    entity_id = str(skill_entity.get("id") or f"agent_skill:{slug}")
    row = {
        "id": entity_id,
        "name": skill_entity.get("name") or slug,
        "source_uri": source_uri,
        "description": skill_entity.get("description"),
    }
    envelope = index_envelope_fields(row)
    parsed_slug = slug_from_source_uri(source_uri) or slug
    raw_description = str(row.get("description") or "").strip()
    description = raw_description or _humanize_slug(parsed_slug)
    return {
        "id": entity_id,
        "slug": parsed_slug,
        "source_uri": envelope.get("source_uri"),
        "digest": envelope.get("digest"),
        "score": _BACKSTOP_SCORE,
        "description": description,
        "reason": "required_skills_backstop",
        "reason_source": "deterministic",
    }


async def apply_required_skills_backstop(
    result: dict[str, Any],
    *,
    entity_ids: list[str] | None,
    loaded: list[str],
) -> dict[str, Any]:
    """Pin bound-entity required_skills into suggestions (todo backstop)."""
    if not entity_ids:
        return result

    loaded_set = build_loaded_set(loaded)
    suggestions = list(result.get("suggestions") or [])
    degraded_skills = list(result.get("degraded_skills") or [])
    present_slugs = {
        norm_loaded(str(item.get("slug") or ""))
        for item in suggestions
        if isinstance(item, dict)
    }
    present_degraded_ids = {
        str(item.get("id") or "")
        for item in degraded_skills
        if isinstance(item, dict)
    }

    async with make_async_client(
        DEFAULT_CORTEX_URL, timeout=_dispatch_config().cortex_timeout_seconds
    ) as client:
        required_slugs = await _collect_required_skill_slugs(entity_ids, client)
        for slug in required_slugs:
            entity_id = f"agent_skill:{slug}"
            if _is_loaded(slug, entity_id, loaded_set):
                continue
            slug_norm = norm_loaded(slug)
            if slug_norm in present_slugs:
                continue

            skill_entity = await _cortex_entity_get(client, entity_id)
            if skill_entity is None:
                continue

            source_uri = _skill_source_uri(skill_entity)
            if not source_uri:
                if entity_id not in present_degraded_ids:
                    attrs = skill_entity.get("attributes") or {}
                    skill_category = ""
                    if isinstance(attrs, dict):
                        skill_category = str(attrs.get("skill_category") or "")
                    degraded_skills.append(
                        {
                            "id": entity_id,
                            "name": str(skill_entity.get("name") or slug),
                            "source_uri": source_uri,
                            "skill_category": skill_category,
                            "degraded": True,
                            "reason": "source_uri_null",
                        }
                    )
                    present_degraded_ids.add(entity_id)
                continue

            suggestions.append(
                _backstop_suggestion(
                    skill_entity=skill_entity,
                    slug=slug,
                    source_uri=source_uri,
                )
            )
            present_slugs.add(slug_norm)

    def _suggestion_sort_key(item: dict[str, Any]) -> tuple[float, str]:
        return (-float(item.get("score") or 0), str(item.get("slug") or ""))

    suggestions.sort(key=_suggestion_sort_key)
    out = dict(result)
    out["suggestions"] = suggestions
    out["degraded_skills"] = degraded_skills
    out["count"] = len(suggestions)
    if degraded_skills:
        out["degraded"] = True
    return out


async def _finalize_dispatch_result(
    result: dict[str, Any], body: SkillSuggestDispatchRequest
) -> dict[str, Any]:
    return await apply_required_skills_backstop(
        result,
        entity_ids=body.entity_ids,
        loaded=body.loaded,
    )


async def _fetch_extended_candidates(
    *,
    agent: str,
    loaded: list[str],
    conversation_context: str | None,
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    """Pre-fetch full seat-applicable candidate set for light-bounded worker."""
    payload: dict[str, Any] = {
        "agent": agent,
        "loaded": loaded,
        "limit": 25,
    }
    if conversation_context is not None:
        payload["conversation_context"] = conversation_context
    async with make_async_client(
        DEFAULT_CORTEX_URL, timeout=_dispatch_config().cortex_timeout_seconds
    ) as client:
        resp = await client.post(
            "/skills/suggest",
            json=payload,
            headers={
                "X-Cortex-Transport": "stargate",
                "X-Skill-Include-All": "true",
            },
        )
    if resp.status_code >= 400:
        logger.warning(
            "skill_suggest extended candidates rejected: status=%s body=%s",
            resp.status_code,
            resp.text[:200],
        )
        return [], [], []
    data = resp.json()
    if not isinstance(data, dict):
        return [], [], []
    candidates = data.get("stage_a_extended_candidates")
    if not isinstance(candidates, list):
        return [], [], []
    loaded_echo = data.get("loaded_echo")
    seat_preloaded = data.get("seat_preloaded")
    return (
        [item for item in candidates if isinstance(item, dict)],
        loaded_echo if isinstance(loaded_echo, list) else [],
        seat_preloaded if isinstance(seat_preloaded, list) else [],
    )


async def run_fallback(
    *,
    agent: str,
    loaded: list[str],
    conversation_context: str | None,
    limit: int,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "agent": agent,
        "loaded": loaded,
        "limit": limit,
    }
    if conversation_context is not None:
        payload["conversation_context"] = conversation_context
    async with make_async_client(
        DEFAULT_CORTEX_URL, timeout=_dispatch_config().cortex_timeout_seconds
    ) as client:
        resp = await client.post(
            "/skills/suggest",
            json=payload,
            headers={"X-Cortex-Transport": "stargate"},
        )
    if resp.status_code >= 400:
        try:
            detail = resp.json()
        except ValueError:
            detail = resp.text
        raise HTTPException(status_code=resp.status_code, detail=detail)
    result: dict[str, Any] = resp.json()
    result["route"] = "fallback"
    result["dispatch_execution_id"] = None
    return result


async def _run_fallback_degraded(
    *,
    agent: str,
    loaded: list[str],
    conversation_context: str | None,
    limit: int,
    degraded_reason: str,
) -> dict[str, Any]:
    result = await run_fallback(
        agent=agent,
        loaded=loaded,
        conversation_context=conversation_context,
        limit=limit,
    )
    result["degraded"] = True
    result["degraded_reason"] = degraded_reason
    return result


def _hallucinated_suggestion_slugs(
    envelope: dict[str, Any], all_candidates: list[dict[str, Any]]
) -> set[str]:
    allowed = {
        str(item.get("slug") or "")
        for item in all_candidates
        if isinstance(item, dict) and item.get("slug")
    }
    suggestion_slugs = {
        str(item.get("slug") or "")
        for item in envelope.get("suggestions") or []
        if isinstance(item, dict) and item.get("slug")
    }
    return suggestion_slugs - allowed


async def dispatch_skill_suggest(
    *,
    request_id: str,
    body: SkillSuggestDispatchRequest,
) -> dict[str, Any]:
    _validate_loaded(body.loaded)
    if (
        body.conversation_context is not None
        and len(body.conversation_context) > _CONTEXT_MAX
    ):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "context_too_large",
                "message": f"conversation_context exceeds {_CONTEXT_MAX} characters",
            },
        )
    canonical_agent = _canonicalize_agent(body.agent)
    if not body.prefer_worker:
        t0 = time.monotonic()
        result = await run_fallback(
            agent=canonical_agent,
            loaded=body.loaded,
            conversation_context=body.conversation_context,
            limit=body.limit,
        )
        _publish_event(
            FrontierSkillSuggestDispatchCompleted(
                request_id=request_id,
                agent=canonical_agent,
                route="fallback",
                latency_ms=int((time.monotonic() - t0) * 1000),
            )
        )
        return await _finalize_dispatch_result(result, body)

    workspaces_root = _workspaces_root().resolve()
    all_candidates, _, _ = await _fetch_extended_candidates(
        agent=canonical_agent,
        loaded=body.loaded,
        conversation_context=body.conversation_context,
    )
    message = build_worker_message(
        loaded=body.loaded,
        conversation_context=body.conversation_context,
        agent=canonical_agent,
        limit=body.limit,
        all_candidates=all_candidates,
    )
    t0 = time.monotonic()
    execution_id: str | None = None
    try:
        dispatch_result = await dispatch_cursor_sdk_generate(
            request_id=request_id,
            role="cursor-sdk",
            model=None,
            subject=f"skill-suggest dispatch — {request_id[:8]}",
            caller_agent=canonical_agent,
            contract="light-bounded",
            packet_path=None,
            message_text=message,
            read_only=True,
        )
    except FrontierEndpointError:
        result = await run_fallback(
            agent=canonical_agent,
            loaded=body.loaded,
            conversation_context=body.conversation_context,
            limit=body.limit,
        )
        _publish_event(
            FrontierSkillSuggestDispatchDegraded(
                request_id=request_id,
                agent=canonical_agent,
                route="fallback",
                reason="dispatch_admission_rejected",
                latency_ms=int((time.monotonic() - t0) * 1000),
            )
        )
        return await _finalize_dispatch_result(result, body)

    execution_id = str(dispatch_result.get("execution_id") or "")
    thread_id = str(dispatch_result.get("thread_id") or "")
    result_handle = dispatch_result.get("result_handle")
    dispatch_durable: bool | None = None
    if isinstance(result_handle, dict):
        durable_val = result_handle.get("durable")
        if isinstance(durable_val, bool):
            dispatch_durable = durable_val
    if not thread_id:
        result = await run_fallback(
            agent=canonical_agent,
            loaded=body.loaded,
            conversation_context=body.conversation_context,
            limit=body.limit,
        )
        _publish_event(
            FrontierSkillSuggestDispatchDegraded(
                request_id=request_id,
                agent=canonical_agent,
                route="fallback",
                reason="missing_thread_id",
                latency_ms=int((time.monotonic() - t0) * 1000),
            )
        )
        return await _finalize_dispatch_result(result, body)

    dispatch_id = str(dispatch_result.get("dispatch_id") or "") or None
    dispatch_cfg = _dispatch_config()
    ledger = load_ledger_snapshot(
        dispatch_id=dispatch_id,
        execution_id=execution_id,
        thread_id=thread_id,
    )

    acked = await await_worker_ack(
        thread_id=thread_id,
        execution_id=execution_id,
        dispatch_id=dispatch_id,
        config=dispatch_cfg,
    )
    if not acked:
        result = await _run_fallback_degraded(
            agent=canonical_agent,
            loaded=body.loaded,
            conversation_context=body.conversation_context,
            limit=body.limit,
            degraded_reason="worker_unreachable",
        )
        _publish_degraded_event(
            request_id=request_id,
            agent=canonical_agent,
            route="fallback",
            reason="worker_unreachable",
            latency_ms=int((time.monotonic() - t0) * 1000),
            execution_id=execution_id or None,
            thread_id=thread_id,
            dispatch_id=dispatch_id,
            last_worker_status=ledger.status if ledger else None,
            last_heartbeat_at=ledger.last_heartbeat_at if ledger else None,
        )
        return await _finalize_dispatch_result(result, body)

    wait_outcome = await await_worker_completion(
        execution_id=execution_id,
        dispatch_id=dispatch_id,
        thread_id=thread_id,
        config=dispatch_cfg,
    )
    ledger = load_ledger_snapshot(
        dispatch_id=dispatch_id,
        execution_id=execution_id,
        thread_id=thread_id,
    )
    closeout_body = await fetch_worker_closeout_body(
        thread_id=thread_id,
        headers=_agent_bus_headers(),
        config=dispatch_cfg,
    )
    envelope = None
    if closeout_body is not None:
        envelope = parse_envelope_from_closeout(
            closeout_body,
            canonical_agent=canonical_agent,
            workspaces_root=workspaces_root,
        )
    degraded_reason = map_wait_outcome_to_degraded_reason(
        wait_outcome,
        ledger=ledger,
        closeout_body=closeout_body,
        envelope_ok=envelope is not None,
    )
    if degraded_reason is not None:
        result = await _run_fallback_degraded(
            agent=canonical_agent,
            loaded=body.loaded,
            conversation_context=body.conversation_context,
            limit=body.limit,
            degraded_reason=degraded_reason,
        )
        _publish_degraded_event(
            request_id=request_id,
            agent=canonical_agent,
            route="fallback",
            reason=degraded_reason,
            latency_ms=int((time.monotonic() - t0) * 1000),
            execution_id=execution_id or None,
            thread_id=thread_id,
            dispatch_id=dispatch_id,
            last_worker_status=ledger.status if ledger else None,
            last_heartbeat_at=ledger.last_heartbeat_at if ledger else None,
        )
        return await _finalize_dispatch_result(result, body)

    assert envelope is not None

    if _hallucinated_suggestion_slugs(envelope, all_candidates):
        degraded_reason = "hallucinated_slugs"
        result = await _run_fallback_degraded(
            agent=canonical_agent,
            loaded=body.loaded,
            conversation_context=body.conversation_context,
            limit=body.limit,
            degraded_reason=degraded_reason,
        )
        _publish_event(
            FrontierSkillSuggestDispatchDegraded(
                request_id=request_id,
                agent=canonical_agent,
                route="fallback",
                reason=degraded_reason,
                latency_ms=int((time.monotonic() - t0) * 1000),
            )
        )
        return await _finalize_dispatch_result(result, body)

    envelope["route"] = "worker"
    envelope["dispatch_execution_id"] = execution_id or None
    envelope["dispatch_durable"] = dispatch_durable
    _publish_event(
        FrontierSkillSuggestDispatchCompleted(
            request_id=request_id,
            agent=canonical_agent,
            route="worker",
            latency_ms=int((time.monotonic() - t0) * 1000),
        )
    )
    return await _finalize_dispatch_result(envelope, body)


@skills_router.post(
    "/suggest-dispatch",
    status_code=200,
    response_model=SkillSuggestDispatchResponse,
    summary="Dispatch skill_suggest via cursor-sdk worker with deterministic fallback",
)
async def post_skill_suggest_dispatch(
    body: SkillSuggestDispatchRequest,
) -> dict[str, Any] | JSONResponse:
    """Worker-hop primary transport to skill_suggest; falls back to Stage-A direct."""
    request_id = uuid.uuid4().hex[:12]
    try:
        return await dispatch_skill_suggest(request_id=request_id, body=body)
    except HTTPException:
        raise
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
