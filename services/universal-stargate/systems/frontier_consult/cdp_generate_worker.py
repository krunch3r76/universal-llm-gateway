"""Background worker + on-behalf delivery for CDP generate."""

from __future__ import annotations

import asyncio
import json
import os
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
    CdpGenerateDeliveryFailed,
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
        result.stall_stage, result.error
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


def format_cdp_result_body(result: CdpGenerateResult) -> str:
    """Render on-behalf turn body from adapter result."""
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
        lines.extend(["", result.body or "_empty harvest_"])
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
    if result.archive_uri:
        lines.append(f"- archive_uri: `{result.archive_uri}`")
    if result.content_proof_uri:
        lines.append(f"- content_proof_uri: `{result.content_proof_uri}`")
    extras = result.extras or {}
    chat_url = extras.get("chat_url")
    if chat_url:
        lines.append(f"- chat_url: `{chat_url}`")
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


async def _mark_cdp_unread_through(
    client: Any,
    *,
    thread_id: str,
    through_turn: int,
    headers: dict[str, str],
) -> None:
    """Mark unread turns for CDP endpoint through ``through_turn``.

    Marks ``web-anthropic`` (canonical) and legacy ``cdp`` (one-cycle compat for
    in-flight pointers still addressed ``to=cdp``).
    """
    through = max(1, int(through_turn))
    for agent in (CDP_REPLY_FROM, "cdp"):
        mark = await client.patch(
            f"/threads/{thread_id}/turns/read-state",
            json={
                "through_turn": through,
                "agent": agent,
            },
            headers=headers,
        )
        if mark.status_code >= 300:
            logger.warning(
                "cdp mark_read before post: thread=%s agent=%s through=%s "
                "status=%s body=%s",
                thread_id,
                agent,
                through,
                mark.status_code,
                mark.text[:200],
            )


def _unread_latest_from_409(resp: Any) -> int | None:
    """Extract ``latest_turn_number`` from an unread_turns_exist 409 body."""
    if getattr(resp, "status_code", None) != 409:
        return None
    try:
        detail = resp.json().get("detail") or {}
    except Exception:  # noqa: BLE001 — non-JSON 409
        return None
    if not isinstance(detail, dict):
        return None
    if detail.get("error") != "unread_turns_exist":
        return None
    try:
        latest = int(detail.get("latest_turn_number") or 0)
    except (TypeError, ValueError):
        return None
    return latest if latest > 0 else None


async def post_cdp_turn(
    *,
    thread_id: str,
    to_agent: str,
    subject: str,
    body: str,
    request_id: str,
    pointer_turn: int = 1,
) -> bool:
    """Post on-behalf bus turn as ``from=web-anthropic`` (endpoint address).

    Marks unread turns addressed to the CDP endpoint through ``pointer_turn``
    before posting — the admit pointer is ``to=web-anthropic`` (legacy
    ``to=cdp`` still marked during one-cycle compat), and agent-bus rejects
    posts while unread (``unread_turns_exist`` 409).

    Concurrent CDP admits on the same root leave later unread turns; on 409,
    remake through ``latest_turn_number`` from the error detail and retry once.
    CDP substrate is carried by ``execution_id`` / ``web-anthropic-cdp``, not
    a separate bus seat.
    """
    token = _agent_bus_token()
    allow_unset = os.getenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    if not token and not allow_unset:
        logger.warning("cdp on-behalf post skipped: AGENT_BUS_TOKEN unset")
        return False
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    through = max(1, int(pointer_turn))
    try:
        async with make_async_client(DEFAULT_AGENT_BUS_URL, timeout=30.0) as client:
            await _mark_cdp_unread_through(
                client,
                thread_id=thread_id,
                through_turn=through,
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
            }
            resp = await client.post("/turns", json=payload, headers=headers)
            if resp.status_code < 300:
                return True
            latest = _unread_latest_from_409(resp)
            if latest is not None and latest > through:
                logger.warning(
                    "cdp on-behalf 409 unread_turns_exist: thread=%s "
                    "pointer=%s latest=%s — remaking + retry",
                    thread_id,
                    through,
                    latest,
                )
                await _mark_cdp_unread_through(
                    client,
                    thread_id=thread_id,
                    through_turn=latest,
                    headers=headers,
                )
                resp = await client.post("/turns", json=payload, headers=headers)
                if resp.status_code < 300:
                    return True
            logger.warning(
                "cdp on-behalf post failed: thread=%s status=%s body=%s",
                thread_id,
                resp.status_code,
                resp.text[:300],
            )
            return False
    except Exception as exc:  # noqa: BLE001 — delivery best-effort
        logger.warning(
            "cdp on-behalf post transport error: thread=%s err=%s request_id=%s",
            thread_id,
            exc,
            request_id,
        )
        return False


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


async def deliver_cdp_result_turn(
    *,
    result: CdpGenerateResult,
    thread_id: str,
    to_agent: str,
    request_id: str,
    pointer_turn: int = 1,
) -> bool:
    """Post result with one retry; then terminal DELIVERY FAILED (fail-closed)."""
    subject = cdp_result_subject(result)
    body = format_cdp_result_body(result)
    posted = await post_cdp_turn(
        thread_id=thread_id,
        to_agent=to_agent,
        subject=subject,
        body=body,
        request_id=request_id,
        pointer_turn=pointer_turn,
    )
    if posted:
        return True
    await asyncio.sleep(_POST_RETRY_SLEEP_S)
    posted = await post_cdp_turn(
        thread_id=thread_id,
        to_agent=to_agent,
        subject=subject,
        body=body,
        request_id=request_id,
        pointer_turn=pointer_turn,
    )
    if posted:
        return True
    fail_subject = f"cdp DELIVERY FAILED — {result.execution_id[:8]}"
    fail_body = format_onbehalf_delivery_failed_body(result)
    final = await post_cdp_turn(
        thread_id=thread_id,
        to_agent=to_agent,
        subject=fail_subject,
        body=fail_body,
        request_id=request_id,
        pointer_turn=pointer_turn,
    )
    if not final:
        logger.critical(
            "cdp on-behalf DELIVERY FAILED post also failed "
            "(bus unreachable residual): thread=%s execution_id=%s "
            "stall_stage=%s request_id=%s",
            thread_id,
            result.execution_id,
            ONBEHALF_POST_FAILED_STALL,
            request_id,
        )
        publish_cdp_kwargs(
            CdpGenerateDeliveryFailed,
            request_id=request_id,
            execution_id=result.execution_id,
            thread_id=thread_id,
            stall_stage=ONBEHALF_POST_FAILED_STALL,
        )
    return final


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
