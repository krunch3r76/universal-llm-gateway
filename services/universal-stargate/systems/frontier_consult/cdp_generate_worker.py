"""Background worker + on-behalf delivery for CDP generate."""

from __future__ import annotations

import asyncio
import os
from typing import Any

from claude_bundles.cdp_model_endpoint import (
    CDP_REPLY_FROM,
    DEFAULT_MAX_WALL_S,
    CdpGenerateResult,
    run_cdp_generate,
)
from transport_utils import DEFAULT_AGENT_BUS_URL, make_async_client
from universal_logging import get_logger

from .cdp_events import (
    CdpGenerateAdmitted,
    CdpGenerateDeliveryFailed,
    CdpGenerateProof,
    CdpGenerateStalled,
    CdpGenerateSubmitted,
    publish_cdp_kwargs,
)

logger = get_logger(__name__)


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
    return "\n".join(
        [
            f"# CDP generate FAILED ({result.picker_model})",
            "",
            f"- execution_id: `{result.execution_id}`",
            f"- satellite_execution_id: `{result.satellite_execution_id}`",
            f"- stall_stage: `{result.stall_stage}`",
            f"- error: {result.error}",
            f"- substrate: `{result.substrate}`",
            f"- cost_source: `{result.cost_source}`",
        ]
    )


async def _mark_cdp_unread_through(
    client: Any,
    *,
    thread_id: str,
    through_turn: int,
    headers: dict[str, str],
) -> None:
    """Mark unread turns addressed to ``cdp`` with turn_number <= through_turn."""
    mark = await client.patch(
        f"/threads/{thread_id}/turns/read-state",
        json={
            "through_turn": max(1, int(through_turn)),
            "agent": CDP_REPLY_FROM,
        },
        headers=headers,
    )
    if mark.status_code >= 300:
        logger.warning(
            "cdp mark_read before post: thread=%s through=%s status=%s body=%s",
            thread_id,
            through_turn,
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
    """Post on-behalf bus turn as ``from=cdp``.

    Marks unread turns addressed to ``cdp`` through ``pointer_turn`` before
    posting — the admit pointer is ``to=cdp``, and agent-bus rejects posts
    while unread (``unread_turns_exist`` 409).

    Concurrent CDP admits on the same root (second ``to=cdp`` pointer after
    this execution's pointer) leave later unread turns; on 409, remake through
    ``latest_turn_number`` from the error detail and retry once.
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
    )
    lines = [format_cdp_result_body(fail)]
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
    subject = (
        f"cdp reply — {result.execution_id[:8]}"
        if result.ok
        else f"cdp FAILED — {result.execution_id[:8]}"
    )
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
) -> None:
    """Stage already done at admit; run adapter and post proof/failure turn."""
    to_agent = caller_agent or "dispatch"
    publish_cdp_kwargs(
        CdpGenerateAdmitted,
        request_id=request_id,
        execution_id=execution_id,
        model=model_id,
        thread_id=thread_id,
    )
    wall = float(max_wall_s) if max_wall_s is not None else DEFAULT_MAX_WALL_S
    # ``on_submitted`` fires inside ``asyncio.to_thread`` (no running loop).
    # ``publish_from_sync`` requires one — hop back via call_soon_threadsafe
    # or the event is swallowed (smoke b1fb4501: admitted+proof, no submitted).
    loop = asyncio.get_running_loop()

    def _on_submitted(satellite_execution_id: str) -> None:
        def _publish() -> None:
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
            on_submitted=_on_submitted,
        )
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

    sat_id = result.satellite_execution_id
    if result.ok:
        publish_cdp_kwargs(
            CdpGenerateProof,
            request_id=request_id,
            execution_id=execution_id,
            satellite_execution_id=sat_id,
            archive_uri=result.archive_uri,
            content_proof_uri=result.content_proof_uri,
        )
    else:
        publish_cdp_kwargs(
            CdpGenerateStalled,
            request_id=request_id,
            execution_id=execution_id,
            satellite_execution_id=sat_id,
            stall_stage=result.stall_stage,
            error=result.error,
        )

    await deliver_cdp_result_turn(
        result=result,
        thread_id=thread_id,
        to_agent=to_agent,
        request_id=request_id,
        pointer_turn=pointer_turn,
    )
