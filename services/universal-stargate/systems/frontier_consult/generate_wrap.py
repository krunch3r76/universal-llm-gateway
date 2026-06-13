"""Gate-then-materialize for the op=generate cursor-sdk implement lane."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi.responses import JSONResponse
from implement_admission.admission_read import frontmatter_value
from implement_admission.preflight import (
    DecisionNotAssertedError,
    require_decision_asserted,
)
from implement_admission.source_ref import SourceRefError

from .cursor_sdk_generate import dispatch_cursor_sdk_generate
from .dispatch_thread_context import read_latest_dispatch_thread_body
from .handoff import _resolve_packet_file, _workspaces_root
from .implement_admission_bridge import (
    StargateCortexReader,
    resolve_source_ref_to_packet,
)
from .implement_ready_gate import require_implement_ready

if TYPE_CHECKING:
    from fastapi import Response

    from .route import TeamDispatchGenerateBody


@dataclass(frozen=True, slots=True)
class GenerateWrapResult:
    packet_path: str | None
    gated: bool = False
    gated_reason: str | None = None
    materialized: bool = False
    warnings: list[str] = field(default_factory=list)


def prepare_implement_packet(
    *,
    request_id: str,
    source_ref: str | None,
    packet_path: str | None,
    caller_agent: str | None,
    cortex: StargateCortexReader,
    workspaces_root: Path,
) -> GenerateWrapResult:
    """Hard-gate, then materialize the implement packet when none was supplied.

    Order is gate-then-materialize (Fork A): require_implement_ready runs first
    on the effective source_ref so a non-dense / unasserted spec is rejected
    BEFORE any materialization side effect. The materialization sub-path
    (no caller packet) additionally runs require_decision_asserted — parity with
    the handoff lane / deprecated seat=cursor-sdk alias it replaces (Fork E).
    Inline packet_path is the fallback and is returned unchanged.
    """
    gate_source_ref = source_ref
    if packet_path:
        packet_file = _resolve_packet_file(workspaces_root.resolve(), packet_path)
        if packet_file is not None:
            gate_source_ref = frontmatter_value(
                packet_file.read_text(encoding="utf-8", errors="replace"),
                "source_ref",
            )

    # HARD GATE (unchanged): triage + implement-ready assertion + dense-spec
    # content + spec_sha256. No-op for non-todo source kinds.
    require_implement_ready(
        request_id=request_id,
        source_ref=gate_source_ref,
        cortex=cortex,
    )

    if packet_path is not None or source_ref is None:
        return GenerateWrapResult(packet_path=packet_path)

    # MASTER RATIFICATION (materialization sub-path only): the unified-admission
    # decision must be ratified before invoking the materialization machinery —
    # parity with the handoff lane + deprecated seat=cursor-sdk alias (GPT-5.5 F1).
    require_decision_asserted(cortex=cortex)

    # FIRST-CLASS WRAP: materialize server-side (reuse the handoff-lane bridge).
    bridge = resolve_source_ref_to_packet(
        source_ref,
        cortex=cortex,
        workspaces_root=workspaces_root,
        request_id=request_id,
        author_family=caller_agent,
    )
    if bridge.gated:
        return GenerateWrapResult(
            packet_path=None,
            gated=True,
            gated_reason=bridge.gated_reason,
        )
    return GenerateWrapResult(
        packet_path=bridge.packet_path,
        materialized=True,
        warnings=list(bridge.warnings),
    )


async def dispatch_cursor_sdk_generate_route(
    *,
    request_id: str,
    body: TeamDispatchGenerateBody,
    role: str,
    response: Response,
) -> dict[str, Any] | JSONResponse:
    """Cursor-sdk generate branch: gate-then-materialize + SDK orchestrator."""
    from .admission import FrontierEndpointError

    try:
        wrap = GenerateWrapResult(packet_path=getattr(body, "packet_path", None))
        if body.contract == "implement":
            loop = asyncio.get_running_loop()
            wrap = await loop.run_in_executor(
                None,
                partial(
                    prepare_implement_packet,
                    request_id=request_id,
                    source_ref=getattr(body, "source_ref", None),
                    packet_path=getattr(body, "packet_path", None),
                    caller_agent=body.caller_agent,
                    cortex=StargateCortexReader(),
                    workspaces_root=_workspaces_root(),
                ),
            )
            if wrap.gated:
                return JSONResponse(
                    status_code=422,
                    content=FrontierEndpointError(
                        request_id=request_id,
                        field="source_ref",
                        reason=wrap.gated_reason or "source_ref not implement-ready",
                        status_code=422,
                        code="generate_source_ref_gated",
                    ).to_dict(),
                )
        source_text = (
            ""
            if body.contract == "implement"
            else await read_latest_dispatch_thread_body(
                request_id=request_id,
                dispatch_thread_id=body.dispatch_thread_id,
            )
        )
        result = await dispatch_cursor_sdk_generate(
            request_id=request_id,
            role=role,
            model=getattr(body, "model", None),
            subject=None,
            caller_agent=body.caller_agent,
            contract=body.contract,
            packet_path=wrap.packet_path,
            message_text=source_text,
            reuse_thread=getattr(body, "reuse_thread", None),
            bus_lifecycle=getattr(body, "bus_lifecycle", None),
        )
        if isinstance(result, dict) and wrap.materialized:
            result["materialization_mode"] = "auto"
            if wrap.warnings:
                result["warnings"] = list(result.get("warnings") or []) + wrap.warnings
    except SourceRefError as exc:
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
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())
    response.status_code = 202
    return result
