"""Admission gates split from route.py. Behavior unchanged."""

from __future__ import annotations

import asyncio
import threading
import uuid
from functools import partial
from typing import Any

from fastapi import Response
from fastapi.responses import JSONResponse

from .admit_provenance import (
    build_team_dispatch_admit_provenance,
    emit_admit_provenance_missing_event,
    finalize_team_dispatch_admit_response,
)
from .cdp_generate import dispatch_cdp_generate
from .cursor_sdk_steer_dispatch import (
    steer_cancel_discard,
    steer_inject_directive,
    steer_park_for_restart,
)
from .dispatch_thread_context import (
    resolve_generate_prompt_body,
)
from .generate_wrap import dispatch_cursor_sdk_generate_route
from .route_models import TeamDispatchBody
from .route_ops import _dispatch, _normalize_op_body
from .route_routers import team_router
from .service import (
    FrontierEndpointError,
    FrontierGenerateRequest,
)


@team_router.post("/dispatch", status_code=202, response_model=None)
async def team_dispatch(
    body: TeamDispatchBody,
    response: Response,
) -> dict[str, Any] | JSONResponse:
    """Persona-required dispatch with explicit op discrimination.

    Two ops:
    - ``op="generate"``: API roles auto-provision bus thread + admit
      ``output_contract=thread``; poll ``poll_hint`` (agent-bus wait).
    - ``op="to_thread"``: caller-owned ``thread``; reply lands on bus after
      dispatch completes.
    - ``op="steer"``: seat-initiated GIW park (``steer="park_for_restart"``).

    Agents use MCP ``team_dispatch`` for all consult surfaces. This HTTP route
    is for Stargate-internal and pipeline-composition callers.
    """
    request_id = uuid.uuid4().hex[:12]
    prompt = getattr(body, "prompt", None)
    if isinstance(prompt, str):
        from agent_bus_store.turns_models import (
            UNEXPANDED_FILE_TOKEN_FIX_HINT,
            text_is_unexpanded_file_token,
        )

        if text_is_unexpanded_file_token(prompt):
            return JSONResponse(
                status_code=422,
                content=FrontierEndpointError(
                    request_id=request_id,
                    field="prompt",
                    reason=UNEXPANDED_FILE_TOKEN_FIX_HINT,
                    status_code=422,
                    code="prompt_unexpanded_file_token",
                    details={"fix_hint": UNEXPANDED_FILE_TOKEN_FIX_HINT},
                ).to_dict(),
            )
    try:
        from ._frontier_intake import reject_unsupported_packet_inputs

        reject_unsupported_packet_inputs(
            request_id=request_id,
            op=body.op,
            contract=getattr(body, "job", None),
            packet_path=getattr(body, "packet_path", None),
            source_ref=getattr(body, "source_ref", None),
            stop_after=(
                (getattr(body, "generation_options", None) or {}).get("stop_after")
            ),
            session=getattr(body, "session", None),
            model=getattr(body, "model", None),
            prompt=getattr(body, "prompt", None),
            sidecar_ref=getattr(body, "sidecar_ref", None),
            mission_kind=getattr(body, "mission_kind", None),
        )
    except FrontierEndpointError as exc:
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())
    admit_provenance = build_team_dispatch_admit_provenance(body, request_id=request_id)
    if admit_provenance is not None:
        emit_admit_provenance_missing_event(admit_provenance)
    if body.op == "ledger" and (
        getattr(body, "house", None) or getattr(body, "parent", None)
    ):
        from cursor_bridge.spawn_endpoint_house_ledger import read_house_ledger

        detail = read_house_ledger(
            house=getattr(body, "house", None),
            parent=getattr(body, "parent", None),
            limit=getattr(body, "limit", 5) or 5,
        )
        response.status_code = 200
        return detail
    if body.op in ("claim", "claim_release", "occupancy", "ledger"):
        from .cursor_sdk_claim_dispatch import relay_house_claim

        holder = (getattr(body, "caller_agent", None) or "").strip()
        if body.op in ("claim", "claim_release") and not holder:
            return JSONResponse(
                status_code=422,
                content=FrontierEndpointError(
                    request_id=request_id,
                    field="caller_agent",
                    reason="caller_agent is required to bind the claim holder",
                    status_code=422,
                    code="holder_agent_required",
                ).to_dict(),
            )
        ok, detail = await relay_house_claim(
            request_id=request_id,
            op=body.op,
            holder_agent=holder,
            work_key=getattr(body, "work_key", None),
            thread_id=getattr(body, "thread", None),
            claim_id=getattr(body, "claim_id", None),
            outcome=getattr(body, "outcome", None),
            ttl_s=getattr(body, "ttl_s", None),
            limit=getattr(body, "limit", None),
            window_hours=getattr(body, "window_hours", None),
        )
        if not ok:
            status_code = int(
                detail.get("status_code") or detail.get("http_status") or 502
            )
            return JSONResponse(status_code=status_code, content=detail)
        response.status_code = int(detail.get("status_code") or 200)
        return detail
    if body.op == "steer":
        if body.steer == "park_for_restart":
            ok, detail = await steer_park_for_restart(
                request_id=request_id,
                dispatch_id=body.dispatch_id,
                reason=body.reason,
                actor=body.actor,
            )
        elif body.steer == "cancel_discard":
            ok, detail = await steer_cancel_discard(
                request_id=request_id,
                dispatch_id=body.dispatch_id,
                reason=body.reason,
                actor=body.actor,
            )
        elif body.steer == "inject":
            if not body.directive or not body.directive.strip():
                return JSONResponse(
                    status_code=422,
                    content=FrontierEndpointError(
                        request_id=request_id,
                        field="directive",
                        reason="directive is required when steer='inject'",
                        status_code=422,
                        code="directive_required",
                    ).to_dict(),
                )
            ok, detail = await steer_inject_directive(
                request_id=request_id,
                dispatch_id=body.dispatch_id,
                directive=body.directive,
                reason=body.reason,
                actor=body.actor,
                ttl_s=body.ttl_s,
            )
        else:
            return JSONResponse(
                status_code=422,
                content=FrontierEndpointError(
                    request_id=request_id,
                    field="steer",
                    reason=f"unsupported steer value: {body.steer!r}",
                    status_code=422,
                    code="steer_unsupported",
                ).to_dict(),
            )
        if not ok:
            status_code = int(
                detail.get("status_code") or detail.get("http_status") or 502
            )
            return JSONResponse(status_code=status_code, content=detail)
        response.status_code = int(detail.get("status_code") or 202)
        return detail
    role = getattr(body, "role", None)
    seat = getattr(body, "seat", None)
    model = getattr(body, "model", None)
    from .wake_lane import validate_wake_lane, wake_lane_needs_probe

    if wake_lane_needs_probe(
        op=body.op,
        wake_lane=getattr(body, "wake_lane", None),
        hop_from=getattr(body, "hop_from", None),
    ):
        try:
            await validate_wake_lane(body.wake_lane, request_id=request_id)
        except FrontierEndpointError as exc:
            return JSONResponse(status_code=exc.status_code, content=exc.to_dict())
    if body.op in ("generate", "to_thread", "spawn"):
        from systems.proxy.dependencies import get_proxy

        from .team_dispatch_admit_client import run_team_dispatch_admit

        _proxy = get_proxy()
        _event_bus = getattr(_proxy, "event_bus", None)
        _admit_loop = asyncio.get_running_loop()

        def _publish_admit_event(event: Any) -> None:
            if _event_bus is None:
                return
            try:
                caller_loop = asyncio.get_running_loop()
            except RuntimeError:
                caller_loop = None
            if caller_loop is _admit_loop:
                _event_bus.publish_from_sync(event)
                return
            done = threading.Event()
            publish_error: list[BaseException] = []

            def _publish_on_admit_loop() -> None:
                try:
                    _event_bus.publish_from_sync(event)
                except BaseException as exc:
                    publish_error.append(exc)
                finally:
                    done.set()

            _admit_loop.call_soon_threadsafe(_publish_on_admit_loop)
            done.wait()
            if publish_error:
                raise publish_error[0]

        try:
            plan = await run_team_dispatch_admit(
                body=body,
                request_id=request_id,
                event_publisher=_publish_admit_event,
            )
        except FrontierEndpointError as exc:
            return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

        role = plan.get("role")
        seat = plan.get("seat")
        model = plan.get("model")
        requested_model_pin = body.model
        if role is not None and hasattr(body, "role"):
            body.role = role
        if seat is not None and hasattr(body, "seat"):
            body.seat = seat
        if model is not None and hasattr(body, "model"):
            body.model = model

        if body.op == "to_thread":
            source_text = await resolve_generate_prompt_body(
                request_id=request_id,
                role=role or "synthesizer",
                dispatch_thread_id=body.dispatch_thread_id,
                prompt=getattr(body, "prompt", None),
                sidecar_ref=getattr(body, "sidecar_ref", None),
            )
            req = FrontierGenerateRequest(
                **_normalize_op_body(body, source_text=source_text)
            )
            result = await _dispatch(req, response)
            return finalize_team_dispatch_admit_response(result, admit_provenance)

        if body.op == "spawn":
            from cursor_bridge.bridge_spawn_relay import relay_spawn_tab
            from cursor_bridge.spawn_endpoint_house_ledger import (
                format_ledger_prompt_prefix,
                patch_spawn_row,
                record_spawn,
            )

            purpose = str(getattr(body, "spawn_purpose", "") or "").strip()
            thread = str(getattr(body, "thread", "") or "").strip()
            spawn_row = record_spawn(
                surface="cursor_ide",
                thread=thread,
                purpose=purpose,
                purpose_unstated=False,
                gui_host=str(body.gui_host),
                state="launching",
                parent={
                    "agent": str(getattr(body, "caller_agent", "") or "").strip(),
                    "thread": thread,
                },
            )
            if not spawn_row.get("ok"):
                return JSONResponse(
                    status_code=500,
                    content={
                        "ok": False,
                        "error": spawn_row.get("error"),
                        "request_id": request_id,
                    },
                )
            row_id = str(spawn_row["row_id"])
            prefixed = format_ledger_prompt_prefix(row_id=row_id, purpose=purpose)
            hop_prompt = prefixed + str(body.prompt)
            loop = asyncio.get_running_loop()
            spawn_res = await loop.run_in_executor(
                None,
                partial(
                    relay_spawn_tab,
                    gui_host=str(body.gui_host),
                    prompt=hop_prompt,
                    model=getattr(body, "model", None),
                    effort=getattr(body, "effort", None),
                    fast=getattr(body, "fast", None),
                    model_knobs=getattr(body, "model_knobs", None),
                    title=getattr(body, "title", None),
                    slug=getattr(body, "slug", None),
                ),
            )
            if spawn_res.get("ok"):
                agent_id = str(
                    spawn_res.get("editor_agent_id")
                    or spawn_res.get("agent_id")
                    or ""
                ).strip()
                patch_spawn_row(
                    row_id,
                    state="live",
                    agent_id=agent_id or None,
                )
            else:
                patch_spawn_row(
                    row_id,
                    state="failed",
                    reason=str(spawn_res.get("error") or spawn_res.get("reason") or ""),
                )
            response.status_code = 200 if spawn_res.get("ok") else 502
            return {
                **spawn_res,
                "ledger_row_id": row_id,
                "thread": thread,
            }

        backend = plan.get("backend")
        if backend == "cdp":
            try:
                result = await dispatch_cdp_generate(
                    request_id=request_id,
                    body=body,
                    response=response,
                    admit_provenance=admit_provenance,
                )
            except FrontierEndpointError as exc:
                return JSONResponse(status_code=exc.status_code, content=exc.to_dict())
            if isinstance(result, dict):
                response.status_code = 202
            return finalize_team_dispatch_admit_response(result, admit_provenance)
        if backend == "cursor_sdk":
            sdk_seat = str(plan.get("seat") or "cursor-sdk")
            if str(getattr(body, "job", "") or "").strip().lower() == "conductor":
                from .admission import FrontierEndpointError as _FrontierEndpointError
                from .conductor_admit_enrich import enrich_conductor_admit_plan

                try:
                    plan = await enrich_conductor_admit_plan(
                        body=body,
                        plan=plan,
                        request_id=request_id,
                    )
                except _FrontierEndpointError as exc:
                    return JSONResponse(
                        status_code=exc.status_code, content=exc.to_dict()
                    )
            result = await dispatch_cursor_sdk_generate_route(
                request_id=request_id,
                body=body,
                seat=sdk_seat,
                response=response,
                requested_model_pin=requested_model_pin,
                admit_provenance=admit_provenance,
                admit_plan=plan,
            )
            return finalize_team_dispatch_admit_response(result, admit_provenance)
        if backend == "api_role":
            from .api_role_generate import dispatch_api_role_generate

            try:
                result = await dispatch_api_role_generate(
                    request_id=request_id,
                    body=body,
                    response=response,
                    admit_provenance=admit_provenance,
                )
            except FrontierEndpointError as exc:
                return JSONResponse(status_code=exc.status_code, content=exc.to_dict())
            if isinstance(result, dict):
                response.status_code = 202
            return finalize_team_dispatch_admit_response(result, admit_provenance)

        source_text = await resolve_generate_prompt_body(
            request_id=request_id,
            role=role or "synthesizer",
            dispatch_thread_id=body.dispatch_thread_id,
            prompt=getattr(body, "prompt", None),
            sidecar_ref=getattr(body, "sidecar_ref", None),
        )
        req = FrontierGenerateRequest(
            **_normalize_op_body(body, source_text=source_text)
        )
        result = await _dispatch(req, response)
        return finalize_team_dispatch_admit_response(result, admit_provenance)

    source_text = await resolve_generate_prompt_body(
        request_id=request_id,
        role=role or "synthesizer",
        dispatch_thread_id=body.dispatch_thread_id,
        prompt=getattr(body, "prompt", None),
        sidecar_ref=getattr(body, "sidecar_ref", None),
    )
    req = FrontierGenerateRequest(**_normalize_op_body(body, source_text=source_text))
    result = await _dispatch(req, response)
    return finalize_team_dispatch_admit_response(result, admit_provenance)

