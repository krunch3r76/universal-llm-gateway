"""Admission gates split from route.py. Behavior unchanged."""

from __future__ import annotations

import asyncio
import uuid
from functools import partial
from typing import Any, Literal

from fastapi import Response
from fastapi.responses import JSONResponse
from implement_admission.preflight import (
    DecisionNotAssertedError,
    require_decision_asserted,
)
from implement_admission.source_ref import SourceRefError
from pydantic import BaseModel

from .admission import (
    enforce_handoff_seat_not_auto,
    resolve_handoff_seat,
    resolve_handoff_target,
)
from .contract_derivation import derive_contract
from .handoff import (
    _workspaces_root,
)
from .implement_admission_bridge import (
    BridgeResult,
    resolve_source_ref_to_packet,
    verify_both_present_hash,
)
from .route_handoff_continue import (
    assemble_handoff_result,
    continue_handoff_admission,
)
from .route_routers import logger, team_router
from .service import (
    FrontierEndpointError,
)
from .stargate_cortex_reader import StargateCortexReader


class TeamHandoffBody(BaseModel):
    """``POST /api/v1/team/handoff`` — create an agent-bus thread for a manual seat.

    ``seat`` — manual seat slug (``claude-web``, ``claude-cursor``, roster aliases).
    ``role`` — handoff roster slug (``web-consult``, ``cursor-implement``, …).
    At least one of ``seat`` or ``role`` is required. Contract is derived server-side
    (F1: explicit ``job`` → ``source_ref`` dispatch_lane → packet front-matter).
    Spec step 3 item 16: an omitted job with no other signal is absent. A role
    does not fill implement. Senders that need implement state ``job=implement``.

    At least one of ``source_ref`` or ``packet_path`` must be present.
    ``source_ref`` triggers normalize→materialize (Phase 2 unified admission).
    ``job`` — optional explicit work-intent, highest-priority signal in F1
    derivation when set. Omitting it does not default to consult, answer, or none.
    """

    model_config = {"extra": "forbid"}

    op: Literal["handoff"]
    role: str | None = None
    seat: str | None = None
    packet_path: str | None = None
    source_ref: str | None = None
    job: str | None = None
    executor_override: str | None = None
    executor_override_reason_code: str | None = None
    executor_override_reason: str | None = None
    subject: str
    pointer_body: str | None = None
    tags: list[str] | None = None
    caller_agent: str | None = None
    bus_lifecycle: Literal["persistent", "ephemeral"] | None = None


@team_router.post("/handoff", status_code=200, response_model=None)
async def team_handoff(
    body: TeamHandoffBody,
    response: Response,
) -> dict[str, Any] | JSONResponse:
    """Create a manual-seat handoff thread; return thread_id synchronously.

    No model dispatch. Web seats: operator pushes agent-bus. Cursor seats:
    operator opens the thread in the IDE. Close your turn with ``push_reminder``.
    """
    request_id = uuid.uuid4().hex[:12]

    if body.source_ref is None and body.packet_path is None:
        return JSONResponse(
            status_code=422,
            content=FrontierEndpointError(
                request_id=request_id,
                field="source_ref",
                reason=(
                    "At least one of source_ref or packet_path is required "
                    "for handoff admission"
                ),
                status_code=422,
                code="handoff_input_underspecified",
            ).to_dict(),
        )

    if body.seat is None and body.role is None:
        return JSONResponse(
            status_code=422,
            content=FrontierEndpointError(
                request_id=request_id,
                field="seat",
                reason="At least one of seat or role is required for handoff admission",
                status_code=422,
                code="handoff_seat_underspecified",
            ).to_dict(),
        )

    loop = asyncio.get_running_loop()
    workspaces_root = _workspaces_root()
    reader = StargateCortexReader()

    packet_path = body.packet_path
    caller_packet_path = body.packet_path
    implement_spec_hash_value: str | None = None
    warnings: list[str] = []
    materialization_present: bool | None = None

    try:
        await loop.run_in_executor(
            None,
            partial(require_decision_asserted, cortex=reader),
        )

        if body.source_ref is not None:
            if packet_path is not None:
                verify_result = await loop.run_in_executor(
                    None,
                    partial(
                        verify_both_present_hash,
                        request_id=request_id,
                        source_ref=body.source_ref,
                        packet_path=packet_path,
                        cortex=reader,
                        workspaces_root=workspaces_root,
                        author_family=body.caller_agent,
                    ),
                )
                implement_spec_hash_value = verify_result.implement_spec_hash
                warnings.extend(verify_result.warnings)
            else:
                bridge_result: BridgeResult = await loop.run_in_executor(
                    None,
                    partial(
                        resolve_source_ref_to_packet,
                        body.source_ref,
                        cortex=reader,
                        workspaces_root=workspaces_root,
                        request_id=request_id,
                        author_family=body.caller_agent,
                        contract=body.job,
                    ),
                )
                if bridge_result.gated:
                    return {
                        "status": "gated",
                        "gated_reason": bridge_result.gated_reason,
                        "source_ref": body.source_ref,
                        "thread_id": None,
                    }
                packet_path = bridge_result.packet_path
                implement_spec_hash_value = bridge_result.implement_spec_hash
                materialization_present = bridge_result.materialization_present
                warnings.extend(bridge_result.warnings)

        assert packet_path is not None

        from systems.proxy.dependencies import get_proxy

        proxy = get_proxy()
        event_bus = getattr(proxy, "event_bus", None)

        def _publish(event: Any) -> None:
            if event_bus is None:
                return
            event_bus.publish_from_sync(event)

        if body.seat is not None:
            try:
                enforce_handoff_seat_not_auto(body.seat, request_id=request_id)
            except FrontierEndpointError as exc:
                return JSONResponse(status_code=exc.status_code, content=exc.to_dict())
            to_agent, family, platform, resolved_model = resolve_handoff_seat(
                seat=body.seat,
                request_id=request_id,
            )
        else:
            to_agent, family, platform, resolved_model = resolve_handoff_target(
                role=body.role or "",
                request_id=request_id,
            )

        handoff_contract, contract_source = await loop.run_in_executor(
            None,
            lambda: derive_contract(
                explicit_contract=body.job,
                source_ref=body.source_ref,
                packet_path=packet_path,
                role=body.role,
                cortex=reader,
                workspaces_root=workspaces_root,
            ),
        )

        admitted = await continue_handoff_admission(
            request_id=request_id,
            body=body,
            packet_path=packet_path,
            contract_source=contract_source,
            workspaces_root=workspaces_root,
            to_agent=to_agent,
            family=family,
            platform=platform,
            resolved_model=resolved_model,
            handoff_contract=handoff_contract,
            reader=reader,
            loop=loop,
            publish=_publish,
            warnings=warnings,
            materialization_present=materialization_present,
        )

    except SourceRefError as exc:
        logger.warning(
            "handoff rejected: request_id=%s field=source_ref code=%s reason=%s",
            request_id,
            exc.code,
            exc,
        )
        return JSONResponse(
            status_code=422,
            content=FrontierEndpointError(
                request_id=request_id,
                field="source_ref",
                reason=f"{exc.rule} ({exc.source_ref})",
                status_code=422,
                code=exc.code,
            ).to_dict(),
        )
    except DecisionNotAssertedError as exc:
        logger.warning(
            "handoff rejected: request_id=%s field=source_ref reason=%s",
            request_id,
            exc,
        )
        return JSONResponse(
            status_code=422,
            content=FrontierEndpointError(
                request_id=request_id,
                field="source_ref",
                reason=str(exc),
                status_code=422,
                code="decision_not_asserted",
            ).to_dict(),
        )
    except FrontierEndpointError as exc:
        logger.warning(
            "handoff rejected: request_id=%s field=%s reason=%s",
            exc.request_id,
            exc.field,
            exc.reason,
        )
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    return assemble_handoff_result(
        request_id=request_id,
        body=body,
        admitted=admitted,
        implement_spec_hash_value=implement_spec_hash_value,
        caller_packet_path=caller_packet_path,
        materialization_present=materialization_present,
        warnings=warnings,
    )
