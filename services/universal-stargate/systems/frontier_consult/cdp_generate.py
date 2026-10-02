"""CDP substrate generate front — ``model=cdp/<picker>`` on team_dispatch.

Thin admit front over the native CDP API (``cdp_ask.client`` /
``POST /api/v1/providers/cdp/ask`` via ``claude_bundles.cdp_model_endpoint``).
Forwards optional ``session`` onto the satellite submit so an operator-proxy
window can set ``session=operator-proxy`` and ``job=freeform`` without bare
``project_ask``. Posts on-behalf turns as ``from=web-anthropic`` (endpoint
address) only after harvest proof (or failed+stall). CDP is substrate/session
association (``web-anthropic-cdp``, ``execution_id``), not a bus seat.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import TYPE_CHECKING, Any

from claude_bundles.cdp_model_endpoint import (
    CDP_REPLY_FROM,
    CDP_SUBSTRATE,
    DEFAULT_MAX_WALL_S,
)
from claude_bundles.cdp_model_endpoint_staging import (
    CdpStagingError,
    stage_cdp_prompt_with_skills,
)
from claude_bundles.cdp_skill_profiles import infer_cdp_purpose
from claude_bundles.chat_model_match import compose_cdp_model_with_effort
from claude_bundles.operator_proxy_mission import is_operator_proxy_mission_purpose
from job_vocab import job_record
from model_id import ModelId

from .admission import FrontierEndpointError
from .cdp_dispatch_envelope import record_cdp_admit
from .cdp_generate_mcp_stamp import (
    publish_cdp_packet_enriched,
    stamp_cdp_packet_mcp_default,
)
from .cdp_generate_reconcile import upsert_inflight_leg
from .cdp_generate_worker import run_cdp_worker
from .cdp_mission_provenance import observe_mission_binding
from .handoff import (
    admit_handoff_dispatch,
    create_handoff_thread,
    post_pointer_turn,
)
from .handoff_response import build_handoff_result, resolve_poll_wait_seconds
from .poll_hint_events import emit_poll_hint_from_handoff

if TYPE_CHECKING:
    from fastapi import Response

    from .route import TeamDispatchGenerateBody

# Retain fire-and-forget CDP worker tasks until done (GC-safe; W9).
_CDP_WORKER_TASKS: set[asyncio.Task[None]] = set()


def is_cdp_model(model: str | None) -> bool:
    """True when ``model`` parses as the CDP substrate (``cdp/<picker>``)."""
    if not model or not str(model).strip():
        return False
    try:
        return ModelId.parse(model).backend_type == "cdp"
    except (TypeError, ValueError):
        return False


_LANE_BIND_PURPOSES = frozenset({"review"})
_GATE_OCCUPANCY_PURPOSES = frozenset(
    {"review", "operator-proxy", "operator_proxy", "mission"}
)


def _binds_operator_lane(purpose: str) -> bool:
    norm = (purpose or "").strip().lower()
    return is_operator_proxy_mission_purpose(purpose) or norm in _LANE_BIND_PURPOSES


def default_operator_seat_binding(
    *,
    purpose: str,
    parent_thread: str | None,
    mission_kind: str | None,
    thread_id: str,
) -> tuple[str | None, str | None]:
    """Default ``parent_thread`` / ``mission_kind`` for gate and operator purposes.

    ``mission_kind="hop"`` is never overwritten. ``parent_thread`` defaults from
    the generate ``thread_id`` when omitted for operator-proxy/mission and for
    ``job=code-review``.
    """
    if not _binds_operator_lane(purpose):
        return parent_thread, mission_kind
    lane = parent_thread or str(thread_id)
    if (mission_kind or "").strip().lower() == "hop":
        kind = "hop"
    else:
        kind = mission_kind or "root"
    return lane, kind


def _read_lane_snapshot_for_gate(*, request_id: str) -> dict[str, Any]:
    """Read active-work for external-gate occupancy at generate fire.

    **Fire-closed vs hop-open (C4):** this probe backs
    ``refuse_second_external_gate_at_fire`` on operator-proxy / review admit.
    Any ``cdp_ask`` active-work fault is **fail-closed** — callers get
    ``FrontierEndpointError`` ``503`` ``cdp_gate_probe_failed`` rather than
    admitting blind. Hop cadence uses the same snapshot reader
    (``read_cdp_lane_snapshot``) but **fail-open** on probe faults so
    succession does not stall when occupancy is temporarily unmeasurable.
    """
    from cdp_ask.lane_snapshot import read_cdp_lane_snapshot

    try:
        return read_cdp_lane_snapshot()
    except Exception as exc:
        raise FrontierEndpointError(
            request_id=request_id,
            field="active-work",
            reason=f"cdp_ask active-work probe failed: {exc}",
            status_code=503,
            code="cdp_gate_probe_failed",
        ) from exc


def _live_external_gate_rows(
    snap: dict[str, Any],
    mission_lane: str,
) -> list[dict[str, Any]]:
    """Live occupancy rows on ``mission_lane`` (same filter as the fire gate)."""
    from claude_bundles.hop_cadence_seat_snap import identity_rows, is_live_stream_state

    lane = (mission_lane or "").strip()
    if not lane or not snap:
        return []
    found: list[dict[str, Any]] = []
    for aw_row in identity_rows(snap):
        stream_state = str(aw_row.get("stream_state") or "")
        if not is_live_stream_state(stream_state):
            continue
        purpose = str(aw_row.get("purpose") or "").strip().lower()
        if purpose not in _GATE_OCCUPANCY_PURPOSES:
            continue
        parent = str(aw_row.get("parent_thread") or "").strip()
        if parent == lane:
            found.append(aw_row)
    return found


def _hop_own_generate_execution_id(
    snap: dict[str, Any],
    mission_lane: str,
    registration_id: str | None,
) -> str | None:
    """Execution of the hopping seat's own live generate, else None.

    A registration match names the caller even when other gates are live;
    those other rows stay blocking. With no registration, the sole live
    gate on the lane is the caller. Two or more live gates and no match
    return None so the gate still refuses — a hop must not clear a second
    successor by dropping every occupant.
    """
    rows = _live_external_gate_rows(snap, mission_lane)
    reg = (registration_id or "").strip()
    if reg:
        for row in rows:
            if str(row.get("registration_id") or "").strip() == reg:
                exec_id = str(row.get("execution_id") or "").strip()
                return exec_id or None
        return None
    if len(rows) == 1:
        exec_id = str(rows[0].get("execution_id") or "").strip()
        return exec_id or None
    return None


def _live_external_gate_for_lane(
    snap: dict[str, Any],
    mission_lane: str,
    *,
    exclude_execution_id: str | frozenset[str] | set[str] | None = None,
) -> bool:
    from claude_bundles.hop_cadence_id_map import (
        ids_match_exclude,
        normalize_exclude_ids,
    )

    exclude = normalize_exclude_ids(exclude_execution_id)
    for aw_row in _live_external_gate_rows(snap, mission_lane):
        exec_id = str(aw_row.get("execution_id") or "").strip()
        if exec_id and ids_match_exclude(exec_id, exclude):
            continue
        return True
    return False


def refuse_second_external_gate_at_fire(
    *,
    purpose: str,
    parent_thread: str | None,
    thread_id: str,
    request_id: str,
    exclude_execution_id: str | frozenset[str] | set[str] | None = None,
    hop_own_generate: bool = False,
    predecessor_registration_id: str | None = None,
    error_field: str = "purpose",
    mission_kind: str | None = None,
) -> None:
    """P1.3 — refuse a second gate while one is still streaming for the lane.

    ``hop_own_generate`` excludes only the hopping seat's own generate
    (registration match, or the sole live gate when registration is absent).
    Any other live gate on the lane still raises ``cdp_external_gate_live``.

    Probe faults on the occupancy read are fail-closed (``cdp_gate_probe_failed``);
    hop cadence intentionally fail-opens the same probe class — see
    ``_read_lane_snapshot_for_gate``.
    """
    if not _binds_operator_lane(purpose):
        return
    lane = (parent_thread or thread_id or "").strip()
    if not lane:
        return
    from claude_bundles.hop_cadence_id_map import normalize_exclude_ids

    snap = _read_lane_snapshot_for_gate(request_id=request_id)
    from cdp_ask.lane_admission import seat_holder_refusal

    seat_refusal = seat_holder_refusal(
        snap,
        lane=lane,
        purpose=purpose,
        mission_kind=mission_kind,
        predecessor_registration_id=predecessor_registration_id,
        hop_succession=hop_own_generate,
    )
    if seat_refusal is not None:
        code = str(seat_refusal.get("code") or "operator_seat_held")
        if code == "seat_unavailable":
            raise FrontierEndpointError(
                request_id=request_id,
                field="active-work",
                reason="seat-axis projection unavailable for external gate",
                status_code=503,
                code="cdp_gate_probe_failed",
            )
        if code != "seat_holder_mismatch":
            from . import cdp_events

            data = seat_refusal.get("data") or {}
            cdp_events.publish_cdp_kwargs(
                cdp_events.CdpGenerateRefusedSeatHeld,
                request_id=request_id,
                lane=lane,
                holder_registration_id=str(data.get("holder_registration_id") or ""),
                purpose=purpose,
                mission_kind=mission_kind,
                observed_at=str(data.get("observed_at") or ""),
            )
            raise FrontierEndpointError(
                request_id=request_id,
                field="purpose",
                reason=str(seat_refusal.get("message") or code),
                status_code=409,
                code="operator_seat_held",
                details=seat_refusal,
            )
        raise FrontierEndpointError(
            request_id=request_id,
            field="purpose",
            reason=str(seat_refusal.get("message") or code),
            status_code=409,
            code=code,
            details=seat_refusal,
        )
    exclude = normalize_exclude_ids(exclude_execution_id)
    if hop_own_generate:
        own = _hop_own_generate_execution_id(snap, lane, predecessor_registration_id)
        if own:
            exclude = exclude | frozenset({own})
    if _live_external_gate_for_lane(snap, lane, exclude_execution_id=exclude):
        raise FrontierEndpointError(
            request_id=request_id,
            field=error_field,
            reason=(
                f"external CDP gate already live for parent_thread={lane!r}; "
                "wait for harvest before firing another gate"
            ),
            status_code=409,
            code="cdp_external_gate_live",
        )


def _refuse_external_gate_for_generate(
    *,
    purpose: str,
    parent_thread: str | None,
    thread_id: str,
    request_id: str,
    execution_id: str,
    mission_kind: str | None,
    predecessor_registration_id: str | None,
    error_field: str = "purpose",
) -> None:
    """Fire-gate. Hop commissions exclude the caller's own generate only."""
    hop = (mission_kind or "").strip().lower() == "hop"
    refuse_second_external_gate_at_fire(
        purpose=purpose,
        parent_thread=parent_thread,
        thread_id=thread_id,
        request_id=request_id,
        exclude_execution_id=execution_id,
        hop_own_generate=hop,
        predecessor_registration_id=(predecessor_registration_id if hop else None),
        error_field=error_field,
        mission_kind=mission_kind,
    )


def reject_cursor_sdk_seat_with_cdp(
    *,
    seat: str | None,
    model: str | None,
    request_id: str,
) -> None:
    """Reject ``seat=cursor-sdk`` + ``model=cdp/…`` (capability positioning)."""
    if not is_cdp_model(model):
        return
    seat_norm = (seat or "").strip().lower()
    if seat_norm in {"cursor-sdk", "cursor_sdk"}:
        raise FrontierEndpointError(
            request_id=request_id,
            field="model",
            reason=(
                "model=cdp/… selects the web-anthropic-cdp substrate and cannot "
                "combine with seat=cursor-sdk"
            ),
            status_code=422,
            code="cdp_cursor_sdk_seat_rejected",
        )


def reject_dispatch_lane_with_cdp(
    *,
    dispatch_lane: str | None,
    model: str | None,
    request_id: str,
) -> None:
    """Reject ``dispatch_lane`` on CDP — code-lane routing token, not life substrate."""
    if not is_cdp_model(model):
        return
    if not dispatch_lane or not str(dispatch_lane).strip():
        return
    raise FrontierEndpointError(
        request_id=request_id,
        field="dispatch_lane",
        reason=(
            "dispatch_lane is a cursor-sdk/todo routing attribute and cannot "
            "combine with model=cdp/… (life substrate); omit dispatch_lane — "
            "use model, contract, and dispatch_thread_id to identify the leg"
        ),
        status_code=422,
        code="cdp_dispatch_lane_rejected",
    )


def reject_role_with_substrate_model(
    *,
    role: str | None,
    model: str | None,
    request_id: str,
) -> None:
    """Reject ``role`` + ``cdp/`` or ``cursor/`` (role would be silently dropped)."""
    if not role or not model:
        return
    try:
        backend = ModelId.parse(model).backend_type
    except (TypeError, ValueError):
        return
    if backend not in {"cdp", "cursor_sdk"}:
        return
    raise FrontierEndpointError(
        request_id=request_id,
        field="role",
        reason=(
            f"role={role!r} cannot combine with substrate model={model!r}; "
            "omit role (model prefix selects transport) or use a cloud model"
        ),
        status_code=422,
        code="substrate_model_role_conflict",
    )


def _stage_inputs(
    *,
    execution_id: str,
    prompt: str | None,
    sidecar_ref: str | None,
    packet_path: str | None,
    skills: list[str] | None = None,
    purpose: str | None = None,
    request_id: str | None = None,
    dispatch_thread_id: str | None = None,
) -> Any:
    """Stage prompt; ``skills`` → slash manifest for + → Skills attach at runtime.

    Judgment-pair skills are always ensured at staging (even when ``skills`` is
    omitted on none CDP generate). Stamps Block 5 MCP defaults for
    life/web before sealing (parity handoff enrich; a:32088).
    """
    cortex_uri = None
    if isinstance(sidecar_ref, str) and sidecar_ref.startswith("cortex://"):
        cortex_uri = sidecar_ref
    elif isinstance(packet_path, str) and packet_path.startswith("cortex://"):
        cortex_uri = packet_path
    packet_non_cortex = (
        packet_path
        if isinstance(packet_path, str) and not packet_path.startswith("cortex://")
        else None
    )
    sidecar_non_cortex = (
        sidecar_ref
        if isinstance(sidecar_ref, str) and not sidecar_ref.startswith("cortex://")
        else None
    )
    stamp = stamp_cdp_packet_mcp_default(
        prompt_text=prompt,
        prompt_uri=cortex_uri,
        packet_path=packet_non_cortex,
        sidecar_ref=sidecar_non_cortex,
    )
    if stamp.stamped and request_id:
        publish_cdp_packet_enriched(
            request_id=request_id,
            source_label=stamp.source_label,
            web_mcp_stamped=True,
        )
    staged_prompt = stamp.body if stamp.stamped else prompt
    staged_uri = None if stamp.stamped else cortex_uri
    staged_packet = None if stamp.stamped else packet_non_cortex
    staged_sidecar = None if stamp.stamped else sidecar_non_cortex
    try:
        return stage_cdp_prompt_with_skills(
            execution_id=execution_id,
            prompt_text=staged_prompt,
            prompt_uri=staged_uri,
            packet_path=staged_packet,
            sidecar_ref=staged_sidecar,
            skills=skills if isinstance(skills, list) else None,
            purpose=purpose,
            dispatch_thread_id=dispatch_thread_id,
        )
    except CdpStagingError:
        raise


async def dispatch_cdp_generate(
    *,
    request_id: str,
    body: TeamDispatchGenerateBody,
    response: Response,
    project_uuid: str | None = None,
) -> dict[str, Any]:
    """Admit CDP generate: return poll_hint immediately; proof posts later."""
    model = compose_cdp_model_with_effort(
        str(body.model or ""),
        getattr(body, "reasoning_effort", None),
    )
    if not is_cdp_model(model):
        raise FrontierEndpointError(
            request_id=request_id,
            field="model",
            reason="dispatch_cdp_generate requires model=cdp/<picker>",
            status_code=422,
            code="cdp_model_required",
        )
    reject_cursor_sdk_seat_with_cdp(
        seat=getattr(body, "seat", None),
        model=model,
        request_id=request_id,
    )
    reject_role_with_substrate_model(
        role=getattr(body, "role", None),
        model=model,
        request_id=request_id,
    )
    reject_dispatch_lane_with_cdp(
        dispatch_lane=getattr(body, "dispatch_lane", None),
        model=model,
        request_id=request_id,
    )
    raw_job = getattr(body, "job", None)
    contract = raw_job.strip() if isinstance(raw_job, str) and raw_job.strip() else None
    if contract in {"implement", "wrap"}:
        raise FrontierEndpointError(
            request_id=request_id,
            field="contract",
            reason="CDP model-endpoint refuses job=implement and job=wrap",
            status_code=422,
            code="cdp_contract_unsupported",
        )

    prompt = getattr(body, "prompt", None)
    sidecar_ref = getattr(body, "sidecar_ref", None)
    packet_path = getattr(body, "packet_path", None)
    if not any([prompt, sidecar_ref, packet_path]):
        from .dispatch_thread_context import resolve_generate_prompt_body

        prompt = await resolve_generate_prompt_body(
            request_id=request_id,
            role=CDP_REPLY_FROM,
            dispatch_thread_id=body.dispatch_thread_id,
            prompt=None,
            sidecar_ref=None,
            packet_path=None,
        )

    if not any([prompt, sidecar_ref, packet_path]):
        raise FrontierEndpointError(
            request_id=request_id,
            field="prompt",
            reason=(
                "CDP generate requires prompt, sidecar_ref, packet_path, "
                "or dispatch_thread body"
            ),
            status_code=422,
            code="cdp_prompt_missing",
        )

    execution_id = str(uuid.uuid4())
    skills = getattr(body, "skills", None)
    purpose_raw = getattr(body, "purpose", None)
    session_raw = getattr(body, "session", None)
    # Omitted session is the judgment floor. Do not infer ``ask`` from the
    # model, and do not read a ``purpose=`` line out of the prompt.
    session_bound = isinstance(session_raw, str) and bool(session_raw.strip())
    if session_bound:
        purpose = session_raw.strip()
    elif isinstance(purpose_raw, str) and purpose_raw.strip():
        purpose = infer_cdp_purpose(purpose_raw.strip(), model)
    else:
        purpose = None
    gate_error_field = "session" if session_bound else "purpose"
    try:
        staged = _stage_inputs(
            execution_id=execution_id,
            prompt=prompt,
            sidecar_ref=sidecar_ref,
            packet_path=packet_path,
            skills=skills if isinstance(skills, list) else None,
            purpose=purpose,
            request_id=request_id,
            dispatch_thread_id=getattr(body, "dispatch_thread_id", None),
        )
    except CdpStagingError as exc:
        if exc.code == "pool_blocked":
            field = "dispatch_thread_id"
        else:
            field = "skills" if str(exc.code).startswith("cdp_skills") else "prompt"
        raise FrontierEndpointError(
            request_id=request_id,
            field=field,
            reason=exc.reason,
            status_code=422,
            code=exc.code,
        ) from exc

    mission_kind_raw = getattr(body, "mission_kind", None)
    mission_kind = (
        str(mission_kind_raw).strip()
        if isinstance(mission_kind_raw, str) and mission_kind_raw.strip()
        else None
    )
    parent_thread_raw = getattr(body, "parent_thread", None)
    parent_thread = (
        str(parent_thread_raw).strip()
        if isinstance(parent_thread_raw, str) and parent_thread_raw.strip()
        else None
    )
    dispatch_thread = body.dispatch_thread_id
    provisional_thread = (
        str(dispatch_thread).strip()
        if dispatch_thread and str(dispatch_thread).strip().isdigit()
        else ""
    )
    declared_parent = parent_thread
    if is_operator_proxy_mission_purpose(purpose):
        parent_thread, mission_kind = default_operator_seat_binding(
            purpose=purpose,
            parent_thread=parent_thread,
            mission_kind=mission_kind,
            thread_id=provisional_thread,
        )
        _refuse_external_gate_for_generate(
            purpose=purpose,
            parent_thread=parent_thread,
            thread_id=provisional_thread,
            request_id=request_id,
            execution_id=execution_id,
            mission_kind=mission_kind,
            predecessor_registration_id=getattr(
                body, "predecessor_registration_id", None
            ),
            error_field=gate_error_field,
        )
    elif (purpose or "").strip().lower() in _LANE_BIND_PURPOSES:
        parent_thread, mission_kind = default_operator_seat_binding(
            purpose=purpose,
            parent_thread=parent_thread,
            mission_kind=mission_kind,
            thread_id=provisional_thread,
        )
        _refuse_external_gate_for_generate(
            purpose=purpose,
            parent_thread=parent_thread,
            thread_id=provisional_thread,
            request_id=request_id,
            execution_id=execution_id,
            mission_kind=mission_kind,
            predecessor_registration_id=getattr(
                body, "predecessor_registration_id", None
            ),
            error_field=gate_error_field,
        )

    thread_subject = f"cdp generate — {request_id}"
    pointer_body = (
        f"CDP generate admitted (model={model}, execution_id={execution_id}). "
        f"Poll poll_hint (from_agent=web-anthropic). Terminal only after harvest proof."
    )

    thread_id = dispatch_thread
    caller_supplied_thread = bool(thread_id and str(thread_id).strip().isdigit())
    if caller_supplied_thread:
        pointer_turn = await post_pointer_turn(
            request_id=request_id,
            thread_id=str(thread_id),
            to_agent=CDP_REPLY_FROM,
            subject=thread_subject,
            pointer_body=pointer_body,
            caller_agent=body.caller_agent,
        )
        after_turn = pointer_turn
    else:
        thread_id = await create_handoff_thread(
            request_id=request_id,
            to_agent=CDP_REPLY_FROM,
            subject=thread_subject,
            pointer_body=pointer_body,
            caller_agent=body.caller_agent,
            tags=[
                "agent:web-anthropic",
                "type:generate",
                *([f"contract:{contract}"] if contract else []),
            ],
            handoff_contract=contract,
            bus_lifecycle=getattr(body, "bus_lifecycle", None),
        )
        after_turn = 1
        if is_operator_proxy_mission_purpose(purpose) or (
            (purpose or "").strip().lower() in _LANE_BIND_PURPOSES
        ):
            parent_thread, mission_kind = default_operator_seat_binding(
                purpose=purpose,
                parent_thread=parent_thread,
                mission_kind=mission_kind,
                thread_id=str(thread_id),
            )

    admit_result = await admit_handoff_dispatch(
        request_id=request_id,
        thread_id=str(thread_id),
        execution_id=execution_id,
        pipeline_id="cdp-generate",
        caller_agent=body.caller_agent,
        parent_thread_id=parent_thread,
    )
    record_cdp_admit(
        execution_id=execution_id,
        thread_id=str(thread_id),
        pointer_turn=after_turn,
        admit_reason=admit_result.reason,
        caller_supplied_thread=caller_supplied_thread,
    )

    timeout_seconds = getattr(body, "timeout_seconds", None)
    max_wall = float(timeout_seconds) if timeout_seconds else DEFAULT_MAX_WALL_S
    upsert_inflight_leg(
        execution_id=execution_id,
        request_id=request_id,
        thread_id=str(thread_id),
        pointer_turn=after_turn,
        caller_agent=body.caller_agent,
        prompt_uri=staged.prompt_uri,
        model_id=str(model),
        max_wall_s=max_wall,
        purpose=purpose,
    )

    if is_operator_proxy_mission_purpose(purpose):
        observe_mission_binding(
            purpose=purpose,
            dispatch_thread_id=str(thread_id),
            parent_thread=parent_thread,
            mission_kind=mission_kind,
            synthesized=declared_parent is None,
        )
    opts = getattr(body, "generation_options", None) or {}
    from .cdp_dispatch_topic import extract_cdp_dispatch_topic

    prompt_for_topic = prompt if isinstance(prompt, str) else None
    dispatch_topic = extract_cdp_dispatch_topic(prompt_for_topic)
    worker_kwargs: dict[str, Any] = {
        "execution_id": execution_id,
        "model_id": str(model),
        "thread_id": str(thread_id),
        "caller_agent": body.caller_agent,
        "prompt_uri": staged.prompt_uri,
        "request_id": request_id,
        "pointer_turn": after_turn,
        "max_wall_s": float(timeout_seconds) if timeout_seconds else None,
        "purpose": purpose,
        "mission_kind": mission_kind,
        "parent_thread": parent_thread,
        "topic": dispatch_topic,
    }
    if contract is not None:
        worker_kwargs["contract"] = contract
    if isinstance(opts, dict):
        if "harvest_source" in opts:
            worker_kwargs["harvest_source"] = opts["harvest_source"]
        if "expected_size" in opts:
            worker_kwargs["expected_size"] = opts["expected_size"]
        if "download_output" in opts:
            worker_kwargs["download_output"] = opts["download_output"]
    if project_uuid:
        worker_kwargs["project_uuid"] = project_uuid
    worker_task = asyncio.create_task(
        run_cdp_worker(**worker_kwargs),
        name=f"cdp-worker-{execution_id[:8]}",
    )
    _CDP_WORKER_TASKS.add(worker_task)

    def _log_worker_done(task: asyncio.Task[None]) -> None:
        _CDP_WORKER_TASKS.discard(task)
        if task.cancelled():
            from universal_logging import get_logger

            get_logger(__name__).warning(
                "cdp worker task cancelled: execution_id=%s",
                execution_id,
            )
            return
        exc = task.exception()
        if exc is not None:
            from universal_logging import get_logger

            get_logger(__name__).error(
                "cdp worker task failed: execution_id=%s err=%s",
                execution_id,
                exc,
                exc_info=exc,
            )

    worker_task.add_done_callback(_log_worker_done)

    record = job_record(contract) if contract else None
    handoff_fields = build_handoff_result(
        thread_id=str(thread_id),
        to_agent=CDP_REPLY_FROM,
        reply_from_agent=CDP_REPLY_FROM,
        after_turn=after_turn,
        poll_wait_seconds=resolve_poll_wait_seconds(caller_agent=body.caller_agent),
        completion="proof_reply_from",
        execution_id=execution_id,
    )
    emit_poll_hint_from_handoff(
        request_id=request_id,
        thread_id=str(thread_id),
        caller_agent=body.caller_agent or "cursor",
        handoff_fields=handoff_fields,
    )
    response.status_code = 202
    return {
        "op": "generate",
        "status": "running",
        "execution_id": execution_id,
        "thread_id": str(thread_id),
        "thread": str(thread_id),
        "to_agent": CDP_REPLY_FROM,
        "reply_from_agent": CDP_REPLY_FROM,
        "resolved_model": model,
        "resolved_job": contract,
        "delivery_role": record.delivery_role if record else "",
        "registry_ref": (
            f"job_vocab:{contract}" if contract else "job_vocab:unresolved"
        ),
        "substrate": CDP_SUBSTRATE,
        "cost_source": "unavailable",
        "prompt_uri": staged.prompt_uri,
        "handoff_status": handoff_fields["handoff_status"],
        "poll_hint": handoff_fields["poll_hint"],
        "result_handle": {
            "kind": "dual",
            "execution_id": execution_id,
            "thread_id": str(thread_id),
            "substrate": CDP_SUBSTRATE,
            "durable": False,
        },
        "capabilities": {
            "role": CDP_REPLY_FROM,
            "resolved_model": model,
            "tool_surface": "cdp",
            "substrate": CDP_SUBSTRATE,
            # inline_only describes Stargate's relay (sealed prompt, no client-side
            # tool loop) and does not bound the endpoint. The claude.ai session owns
            # its own connectors, so tool access is always true for CDP models.
            "inline_only": True,
            "tool_access": True,
        },
        "terminal": False,
    }
