"""CDP on-behalf success delivery — oversized harvest spill to cortex pointer."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from claude_bundles.cdp_model_endpoint import CdpGenerateResult
from universal_logging import get_logger

logger = get_logger(__name__)

BUS_MAX_BODY_CHARS = 64_000
_POST_RETRY_SLEEP_S = 0.5


@dataclass(frozen=True)
class SidecarResult:
    uri: str
    sha256: str
    body_chars: int


def extract_pointer_summary(content: str, *, max_chars: int = 300) -> str | None:
    """First heading + sentence for relocation pointer preview."""
    body = content.lstrip()
    if body.startswith("---"):
        end = body.find("\n---", 3)
        if end != -1:
            body = body[end + 4 :].lstrip()
    if not body:
        return None
    lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
    heading = None
    for ln in lines:
        if ln.startswith("#"):
            heading = ln.lstrip("#").strip()
            break
    prose = next(
        (ln for ln in lines if not ln.startswith(("#", "```", "|", "-", "*"))),
        "",
    )
    sentence = prose.split(". ")[0].strip()
    out = " — ".join(p for p in (heading, sentence) if p) or body
    return out[:max_chars].rstrip()


def build_relocation_pointer(
    result: CdpGenerateResult,
    *,
    sidecar_uri: str,
    sha256: str,
    body_chars: int,
    summary: str | None,
) -> str:
    """Compact bus turn body citing durable harvest archive."""
    parts = [
        "**Full CDP harvest relocated to cortex (not lost).** "
        f"Body was {body_chars} chars (bus limit {BUS_MAX_BODY_CHARS}).",
        "",
        f"- Durable copy: `{sidecar_uri}`",
        f"- sha256: `{sha256}`",
        f"- execution: `{result.execution_id}`",
    ]
    if summary:
        parts += ["", "**Summary:**", "", summary]
    rel = sidecar_uri.removeprefix("cortex://")
    parts += [
        "",
        f"_Read the full content: fs(cortex, read, {rel})_",
    ]
    return "\n".join(parts)


async def write_cdp_harvest_sidecar(
    *,
    result: CdpGenerateResult,
    thread_id: str,
    subject: str,
    content: str,
    oversized: bool,
) -> SidecarResult | None:
    """Persist harvest to cortex thread sidecar before pointer post."""
    from systems.pipeline.core.handlers.thread_persistence.events import cx_async

    sidecar_result = await cx_async(
        "thread_sidecar_write",
        {
            "thread": thread_id,
            "subject": subject,
            "content": content,
            "from_agent": "web-anthropic",
            "execution_id": result.execution_id,
            "oversized": oversized,
        },
    )
    if "error" in sidecar_result:
        logger.error(
            "CDP on-behalf sidecar write failed: execution_id=%s thread=%s error=%s",
            result.execution_id,
            thread_id,
            sidecar_result.get("error"),
        )
        return None
    return SidecarResult(
        uri=sidecar_result["uri"],
        sha256=sidecar_result["sha256"],
        body_chars=sidecar_result["body_chars"],
    )


async def build_cdp_success_delivery_body(
    *,
    result: CdpGenerateResult,
    thread_id: str,
    subject: str,
    format_metadata: str,
) -> str | None:
    """Build ok=True delivery body; None when oversized sidecar write failed."""
    harvest = result.body or "_empty harvest_"
    inline_body = f"{format_metadata}\n\n{harvest}"
    if len(inline_body) <= BUS_MAX_BODY_CHARS:
        return inline_body

    body_chars = len(harvest)
    summary = extract_pointer_summary(harvest)

    if result.archive_uri:
        sha256 = result.content_proof_sha256 or "unavailable"
        pointer = build_relocation_pointer(
            result,
            sidecar_uri=result.archive_uri,
            sha256=sha256,
            body_chars=body_chars,
            summary=summary,
        )
        return f"{format_metadata}\n\n{pointer}"

    sidecar = await write_cdp_harvest_sidecar(
        result=result,
        thread_id=thread_id,
        subject=subject,
        content=harvest,
        oversized=True,
    )
    if sidecar is None:
        return None

    pointer = build_relocation_pointer(
        result,
        sidecar_uri=sidecar.uri,
        sha256=sidecar.sha256,
        body_chars=body_chars,
        summary=summary,
    )
    return f"{format_metadata}\n\n{pointer}"


async def deliver_cdp_result_turn(
    *,
    result: CdpGenerateResult,
    thread_id: str,
    to_agent: str,
    request_id: str,
    pointer_turn: int = 1,
) -> bool:
    """Post CDP result with spill/pointer for oversized ok=True harvests."""
    from .cdp_events import CdpGenerateDeliveryFailed, publish_cdp_kwargs
    from .cdp_generate_worker import (
        ONBEHALF_POST_FAILED_STALL,
        cdp_result_subject,
        format_cdp_result_body,
        format_onbehalf_delivery_failed_body,
        post_cdp_turn,
    )

    subject = cdp_result_subject(result)
    if result.ok:
        metadata = format_cdp_result_body(result)
        body = await build_cdp_success_delivery_body(
            result=result,
            thread_id=thread_id,
            subject=subject,
            format_metadata=metadata,
        )
        if body is None:
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
    else:
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
