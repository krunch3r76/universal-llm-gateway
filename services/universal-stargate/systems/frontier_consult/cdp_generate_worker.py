"""Background worker + on-behalf delivery for CDP generate."""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from typing import Any

from cdp_ask.unverifiable import is_unverifiable_stall
from claude_bundles.cdp_model_endpoint import (
    CDP_REPLY_FROM,
    DEFAULT_MAX_WALL_S,
    UPSTREAM_OVERLOADED,
    CdpGenerateResult,
    run_cdp_generate,
)
from transport_utils import DEFAULT_AGENT_BUS_URL, DEFAULT_CORTEX_URL, make_async_client
from universal_logging import get_logger

from .cdp_events import (
    CdpGenerateAdmitted,
    CdpGenerateSubmitted,
    publish_cdp_kwargs,
)

logger = get_logger(__name__)

_UPSTREAM_OVERLOAD_FRICTION_EMITTED: set[str] = set()
_CORTEX_FRICTION_TIMEOUT_S = 10.0


def _upstream_overloaded(result: CdpGenerateResult) -> bool:
    """True when adapter stamped upstream overload on the result carrier."""
    if result.stall_stage == UPSTREAM_OVERLOADED:
        return True
    return (result.extras or {}).get("reason") == UPSTREAM_OVERLOADED


def cdp_result_unverified(result: CdpGenerateResult) -> bool:
    """True when the envelope is observer-unverifiable, not CSE death."""
    return (not result.ok) and is_unverifiable_stall(
        result.stall_stage,
        result.error,
        url=(result.extras or {}).get("chat_url"),
        satellite_execution_id=result.satellite_execution_id,
    )


def cdp_result_subject(result: CdpGenerateResult) -> str:
    """On-behalf bus subject for a generate result."""
    short = result.execution_id[:8]
    if result.ok:
        return f"cdp reply — {short}"
    if cdp_result_unverified(result):
        return f"cdp UNVERIFIED — {short}"
    return f"cdp FAILED — {short}"


def _agent_bus_token() -> str:
    return os.getenv("AGENT_BUS_TOKEN", "").strip()


def format_cdp_result_body(
    result: CdpGenerateResult,
    *,
    thread_id: str | None = None,
    pointer_turn: int | None = None,
    dispatch_link: str | None = None,
) -> str:
    """Render on-behalf turn body from adapter result."""
    extras = result.extras or {}
    thread_id = thread_id or extras.get("thread_id")
    pointer_turn = pointer_turn if pointer_turn is not None else extras.get("pointer_turn")
    dispatch_link = dispatch_link or extras.get("dispatch_link")
    if result.ok:
        lines = [
            f"# CDP generate result ({result.picker_model})",
            "",
            f"- execution_id: `{result.execution_id}`",
            f"- satellite_execution_id: `{result.satellite_execution_id}`",
            f"- substrate: `{result.substrate}`",
            f"- cost_source: `{result.cost_source}`",
        ]
        if result.archive_uri:
            lines.append(f"- archive_uri: `{result.archive_uri}`")
        if result.content_proof_uri:
            lines.append(f"- content_proof_uri: `{result.content_proof_uri}`")
        return "\n".join(lines)
    heading = "UNVERIFIED" if cdp_result_unverified(result) else "FAILED"
    lines = [
        f"# CDP generate {heading} ({result.picker_model})",
        "",
        f"- execution_id: `{result.execution_id}`",
        f"- satellite_execution_id: `{result.satellite_execution_id}`",
        f"- stall_stage: `{result.stall_stage}`",
        f"- error: {result.error}",
        f"- body_len: {len(result.body or '')}",
        f"- substrate: `{result.substrate}`",
        f"- cost_source: `{result.cost_source}`",
    ]
    if thread_id is not None:
        lines.append(f"- thread_id: `{thread_id}`")
    if pointer_turn is not None:
        lines.append(f"- pointer_turn: `{pointer_turn}`")
    if dispatch_link:
        lines.append(f"- dispatch_link: {dispatch_link}")
    registration_id = extras.get("registration_id")
    chat_url = extras.get("chat_url")
    registry_status = extras.get("registry_status")
    if registration_id or chat_url:
        parts = []
        if registration_id:
            parts.append(str(registration_id))
        if chat_url:
            parts.append(str(chat_url))
        if registry_status:
            parts.append(str(registry_status))
        lines.append(f"- cse: {' · '.join(parts)} (seat fact)")
    else:
        lines.append("- cse: none for this execution")
    if result.archive_uri:
        lines.append(f"- archive_uri: `{result.archive_uri}`")
    if result.content_proof_uri:
        lines.append(f"- content_proof_uri: `{result.content_proof_uri}`")
    if extras.get("deliverable_present_unproven"):
        lines.append("- deliverable_present_unproven: true")
        recovery = extras.get("recovery")
        if recovery:
            lines.append(f"- recovery: {recovery}")
    if _upstream_overloaded(result):
        lines.extend(
            [
                "",
                "status:failed reason=upstream_overloaded",
                f"- reason: `{UPSTREAM_OVERLOADED}`",
            ]
        )
    return "\n".join(lines)


@dataclass(frozen=True)
class PostOutcome:
    """Result of one on-behalf POST /turns attempt."""

    ok: bool
    http_status: int | None = None
    detail_preview: str | None = None


async def _mark_pointer_turn_read(
    client: Any,
    *,
    thread_id: str,
    pointer_turn: int,
    headers: dict[str, str],
) -> None:
    """Mark read only the admit pointer turn (exact turn_number)."""
    turn_number = max(1, int(pointer_turn))
    mark = await client.patch(
        f"/threads/{thread_id}/turns/read-state",
        json={"turn_numbers": [turn_number]},
        headers=headers,
    )
    if mark.status_code >= 300:
        logger.warning(
            "cdp mark_read pointer before post: thread=%s turn=%s "
            "status=%s body=%s",
            thread_id,
            turn_number,
            mark.status_code,
            mark.text[:200],
        )


async def post_cdp_turn(
    *,
    thread_id: str,
    to_agent: str,
    subject: str,
    body: str,
    request_id: str,
    pointer_turn: int = 1,
) -> PostOutcome:
    """Post on-behalf bus turn as ``from=web-anthropic`` (endpoint address).

    Marks only the admit pointer turn read, then posts with ``on_behalf: true``
    so the poster inbox gate is bypassed. CDP substrate is carried by
    ``execution_id`` / ``web-anthropic-cdp``, not a separate bus seat.
    """
    token = _agent_bus_token()
    allow_unset = os.getenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    if not token and not allow_unset:
        logger.warning("cdp on-behalf post skipped: AGENT_BUS_TOKEN unset")
        return PostOutcome(ok=False, http_status=None, detail_preview="token_unset")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    through = max(1, int(pointer_turn))
    try:
        async with make_async_client(DEFAULT_AGENT_BUS_URL, timeout=30.0) as client:
            await _mark_pointer_turn_read(
                client,
                thread_id=thread_id,
                pointer_turn=through,
                headers=headers,
            )
            payload: dict[str, Any] = {
                "thread": thread_id,
                "from": CDP_REPLY_FROM,
                "to": to_agent,
                "subject": subject,
                "body": body,
                "status": "open",
                "after_turn": 0,
                "allow_long_body": True,
                "on_behalf": True,
            }
            resp = await client.post("/turns", json=payload, headers=headers)
            if resp.status_code < 300:
                return PostOutcome(ok=True, http_status=resp.status_code)
            preview = resp.text[:300]
            logger.warning(
                "cdp on-behalf post failed: thread=%s status=%s body=%s",
                thread_id,
                resp.status_code,
                preview,
            )
            return PostOutcome(
                ok=False,
                http_status=resp.status_code,
                detail_preview=preview,
            )
    except Exception as exc:  # noqa: BLE001 — delivery best-effort
        logger.warning(
            "cdp on-behalf post transport error: thread=%s err=%s request_id=%s",
            thread_id,
            exc,
            request_id,
        )
        return PostOutcome(
            ok=False,
            http_status=None,
            detail_preview=str(exc)[:300],
        )


_POST_RETRY_SLEEP_S = 0.5
ONBEHALF_POST_FAILED_STALL = "onbehalf_post_failed"


def format_onbehalf_delivery_failed_body(result: CdpGenerateResult) -> str:
    """Terminal body when harvest exists but on-behalf bus post failed."""
    prior_stall = result.stall_stage
    prior_reason = (result.extras or {}).get("reason")
    merged_extras = dict(result.extras or {})
    fail = CdpGenerateResult(
        ok=False,
        body=result.body or "",
        execution_id=result.execution_id,
        satellite_execution_id=result.satellite_execution_id,
        prompt_uri=result.prompt_uri,
        picker_model=result.picker_model,
        archive_uri=result.archive_uri,
        content_proof_uri=result.content_proof_uri,
        content_proof_sha256=result.content_proof_sha256,
        stall_stage=ONBEHALF_POST_FAILED_STALL,
        error=result.error or "on-behalf bus post failed after retry",
        substrate=result.substrate,
        cost_source=result.cost_source,
        extras=merged_extras,
    )
    lines = [format_cdp_result_body(fail)]
    if prior_stall and prior_stall != ONBEHALF_POST_FAILED_STALL:
        lines.append(f"- prior_stall_stage: `{prior_stall}`")
    if prior_reason:
        lines.append(f"- prior_reason: `{prior_reason}`")
    if result.archive_uri:
        lines.append(f"- prior_archive_uri: `{result.archive_uri}`")
    if result.content_proof_uri:
        lines.append(f"- prior_content_proof_uri: `{result.content_proof_uri}`")
    return "\n".join(lines)


from .cdp_onbehalf_delivery import deliver_cdp_result_turn  # noqa: E402, F401


async def _emit_upstream_overload_friction(
    *,
    execution_id: str,
    thread_id: str,
    result: CdpGenerateResult,
) -> None:
    """Best-effort cortex friction row when upstream overload exhausts (deduped)."""
    if execution_id in _UPSTREAM_OVERLOAD_FRICTION_EMITTED:
        return
    _UPSTREAM_OVERLOAD_FRICTION_EMITTED.add(execution_id)
    status_code = (result.extras or {}).get("status_code")
    note = (
        f"CDP generate upstream overload exhaust "
        f"execution_id={execution_id} thread_id={thread_id} "
        f"status_code={status_code} attempt=exhaust"
    )
    payload = {
        "tool": "friction",
        "arguments": json.dumps(
            {
                "owner": "service:universal-stargate",
                "category": "tool_error",
                "agent": "cdp-generate-worker",
                "actionable": True,
                "note": note,
            }
        ),
    }
    try:
        async with make_async_client(
            DEFAULT_CORTEX_URL, timeout=_CORTEX_FRICTION_TIMEOUT_S
        ) as client:
            resp = await client.post("/dispatch", json=payload)
        if resp.status_code >= 300:
            logger.warning(
                "cdp upstream overload friction failed: "
                "execution_id=%s status=%s body=%s",
                execution_id,
                resp.status_code,
                resp.text[:200],
            )
    except Exception as exc:  # noqa: BLE001 — friction is best-effort
        logger.warning(
            "cdp upstream overload friction transport error: execution_id=%s err=%s",
            execution_id,
            exc,
        )


async def run_cdp_worker(
    *,
    execution_id: str,
    model_id: str,
    thread_id: str,
    caller_agent: str | None,
    prompt_uri: str,
    request_id: str,
    pointer_turn: int = 1,
    max_wall_s: float | None = None,
    harvest_source: str = "auto",
    expected_size: str = "auto",
    download_output: bool = False,
    purpose: str = "ask",
    mission_kind: str | None = None,
    parent_thread: str | None = None,
    topic: str | None = None,
    project_uuid: str | None = None,
) -> None:
    """Stage already done at admit; run adapter and post proof/failure turn."""
    from .cdp_generate_reconcile import (
        attach_satellite_execution_id,
        finalize_cdp_generate,
    )

    to_agent = caller_agent or "dispatch"
    publish_cdp_kwargs(
        CdpGenerateAdmitted,
        request_id=request_id,
        execution_id=execution_id,
        model=model_id,
        thread_id=thread_id,
        topic=topic,
    )
    wall = float(max_wall_s) if max_wall_s is not None else DEFAULT_MAX_WALL_S
    loop = asyncio.get_running_loop()

    def _on_submitted(satellite_execution_id: str) -> None:
        def _publish() -> None:
            attach_satellite_execution_id(
                execution_id=execution_id,
                satellite_execution_id=satellite_execution_id,
            )
            publish_cdp_kwargs(
                CdpGenerateSubmitted,
                request_id=request_id,
                execution_id=execution_id,
                satellite_execution_id=satellite_execution_id,
                model=model_id,
            )

        loop.call_soon_threadsafe(_publish)

    try:
        result = await asyncio.to_thread(
            run_cdp_generate,
            execution_id=execution_id,
            model_id=model_id,
            prompt_uri=prompt_uri,
            max_wall_s=wall,
            harvest_source=harvest_source,  # type: ignore[arg-type]
            expected_size=expected_size,  # type: ignore[arg-type]
            download_output=download_output,
            purpose=purpose,
            mission_kind=mission_kind,
            parent_thread=parent_thread,
            project_uuid=project_uuid,
            on_submitted=_on_submitted,
        )
    except asyncio.CancelledError:
        leg_satellite: str | None = None
        from .cdp_generate_reconcile import read_inflight_leg

        leg = read_inflight_leg(execution_id)
        if leg is not None:
            leg_satellite = leg.satellite_execution_id
        cancelled = CdpGenerateResult(
            ok=False,
            body="",
            execution_id=execution_id,
            satellite_execution_id=leg_satellite,
            prompt_uri=prompt_uri,
            picker_model=model_id.split("/", 1)[-1],
            stall_stage="worker_cancelled",
            error="CDP worker task cancelled",
        )
        await finalize_cdp_generate(
            result=cancelled,
            request_id=request_id,
            thread_id=thread_id,
            to_agent=to_agent,
            pointer_turn=pointer_turn,
            via="worker",
        )
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("cdp worker crashed: execution_id=%s", execution_id)
        result = CdpGenerateResult(
            ok=False,
            body="",
            execution_id=execution_id,
            satellite_execution_id=None,
            prompt_uri=prompt_uri,
            picker_model=model_id.split("/", 1)[-1],
            error=f"worker_crash: {exc}",
        )

    await finalize_cdp_generate(
        result=result,
        request_id=request_id,
        thread_id=thread_id,
        to_agent=to_agent,
        pointer_turn=pointer_turn,
        via="worker",
    )
