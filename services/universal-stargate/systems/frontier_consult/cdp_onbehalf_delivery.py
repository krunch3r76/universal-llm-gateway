"""CDP on-behalf success delivery — oversized harvest spill to cortex pointer."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass

from bus_compact_pointer import (
    BodyMode,
    build_section_map,
    compact_pointer_body,
    extract_verdict_line,
)
from claude_bundles.cdp_model_endpoint import CdpGenerateResult
from services.git_integration_worker.cursor_sdk_closeout_harvest import _fenced_spans
from universal_logging import get_logger

logger = get_logger(__name__)

BUS_MAX_BODY_CHARS = 64_000
_POST_RETRY_SLEEP_S = 0.5

# Same shape as master_review_owed.CANONICAL_VERDICT_RE (binder contract).
_CANONICAL_VERDICT_RE = re.compile(
    r"(?im)^VERDICT:[ \t]+(APPROVE|REQUEST_CHANGES)[ \t]+"
    r"range=([0-9a-f]{40})\.\.([0-9a-f]{40})[ \t]+head=\3"
    r"(?:[ \t]+fixes=([0-9a-f]{40}))?[ \t]*$"
)


def collapse_identical_unfenced_canonical_verdicts(harvest: str) -> str:
    """Drop repeat unfenced canonical VERDICT lines; keep first; fences unchanged."""
    if not harvest:
        return harvest
    spans = _fenced_spans(harvest)
    seen: set[str] = set()
    kept: list[str] = []
    offset = 0
    for line in harvest.splitlines(keepends=True):
        in_fence = any(start <= offset < end for start, end in spans)
        stripped = line.rstrip("\r\n")
        if (
            not in_fence
            and _CANONICAL_VERDICT_RE.fullmatch(stripped) is not None
            and stripped in seen
        ):
            offset += len(line)
            continue
        if (
            not in_fence
            and _CANONICAL_VERDICT_RE.fullmatch(stripped) is not None
        ):
            seen.add(stripped)
        kept.append(line)
        offset += len(line)
    return "".join(kept)


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


def _metadata_protected_lines(format_metadata: str) -> list[str]:
    return [ln.strip() for ln in format_metadata.splitlines() if ln.strip()]


def build_relocation_pointer(
    result: CdpGenerateResult,
    *,
    sidecar_uri: str,
    sha256: str,
    body_chars: int,
    summary: str | None,
    sidecar_markdown: str | None = None,
    format_metadata: str = "",
) -> str:
    """Compact bus turn body citing durable harvest archive."""
    text = sidecar_markdown or ""
    section_map, hidden = build_section_map(text) if text else ([], 0)
    original_rows = len(section_map) + hidden
    tail: str | None = None
    if hidden:
        rel = sidecar_uri.removeprefix("cortex://")
        tail = f"+{hidden} more (md_list {rel})"
    verdict = extract_verdict_line(text) if text else None
    if verdict is None:
        verdict = "RELOCATED"
    context: list[str] = [
        f"harvest_chars: {body_chars}",
        f"picker: {result.picker_model}",
    ]
    if summary:
        context.append(summary[:240])
    return compact_pointer_body(
        verdict=verdict,
        execution_id=result.execution_id,
        satellite_execution_id=result.satellite_execution_id,
        archive_uri=result.archive_uri,
        content_proof_uri=result.content_proof_uri,
        protected_lines=_metadata_protected_lines(format_metadata),
        sidecar_uri=sidecar_uri,
        sidecar_sha256=sha256,
        section_map=section_map,
        context_lines=context,
        section_tail=tail,
        section_map_source_rows=original_rows if text else 0,
    )


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
    pointer_only: bool = False,
    body_mode: BodyMode = "compact",
) -> str | None:
    """Build ok=True delivery body; None when oversized sidecar write failed.

  ``body_mode=compact`` (default) always sidecars harvest and posts a compact
    pointer. ``inline`` preserves legacy inline harvest up to ``BUS_MAX_BODY_CHARS``.
    ``pointer_only`` keeps the legacy minimal pointer (no section map / summary).
    """
    if pointer_only and body_mode == "compact":
        body_mode = "pointer_only"
    raw_harvest = result.body
    harvest = (
        collapse_identical_unfenced_canonical_verdicts(raw_harvest)
        if raw_harvest
        else "_empty harvest_"
    )
    if body_mode == "inline":
        inline_body = f"{format_metadata}\n\n{harvest}"
        if len(inline_body) <= BUS_MAX_BODY_CHARS:
            return inline_body
        body_mode = "compact"

    if body_mode in ("compact", "pointer_only"):
        body_chars = len(result.body or "")
        store_content = harvest
        sidecar_uri: str
        sidecar_sha: str
        map_source = store_content
        if result.archive_uri and result.content_proof_sha256:
            sidecar_uri = result.archive_uri
            sidecar_sha = result.content_proof_sha256
            map_source = ""
        else:
            sidecar = await write_cdp_harvest_sidecar(
                result=result,
                thread_id=thread_id,
                subject=subject,
                content=store_content,
                oversized=body_mode == "compact",
            )
            if sidecar is None:
                return None
            sidecar_uri = sidecar.uri
            sidecar_sha = sidecar.sha256
            map_source = store_content

        meta_lines = _metadata_protected_lines(format_metadata)

        if body_mode == "pointer_only":
            return build_relocation_pointer(
                result,
                sidecar_uri=sidecar_uri,
                sha256=sidecar_sha,
                body_chars=body_chars,
                summary=None,
                sidecar_markdown=None,
                format_metadata=format_metadata,
            )

        section_map, hidden = (
            build_section_map(map_source) if map_source else ([], 0)
        )
        original_rows = len(section_map) + hidden
        tail: str | None = None
        if hidden:
            rel = sidecar_uri.removeprefix("cortex://")
            tail = f"+{hidden} more (md_list {rel})"
        verdict = extract_verdict_line(store_content) or "OK"
        context = [
            f"picker: {result.picker_model}",
            f"harvest_chars: {body_chars}",
        ]
        pointer = compact_pointer_body(
            verdict=verdict,
            execution_id=result.execution_id,
            satellite_execution_id=result.satellite_execution_id,
            archive_uri=result.archive_uri,
            content_proof_uri=result.content_proof_uri,
            protected_lines=meta_lines,
            sidecar_uri=sidecar_uri,
            sidecar_sha256=sidecar_sha,
            section_map=section_map,
            context_lines=context,
            section_tail=tail,
            section_map_source_rows=original_rows,
        )
        return pointer

    return None


async def deliver_cdp_result_turn(
    *,
    result: CdpGenerateResult,
    thread_id: str,
    to_agent: str,
    request_id: str,
    pointer_turn: int = 1,
    pointer_only: bool = False,
    body_mode: BodyMode = "compact",
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
            pointer_only=pointer_only,
            body_mode=body_mode,
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
            if not final.ok:
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
                    http_status=final.http_status,
                    detail_preview=final.detail_preview,
                )
            return final.ok
    else:
        body = format_cdp_result_body(result)

    allow_long = body_mode == "inline" and len(body) > 1500
    post_extra = {"allow_long_body": True} if allow_long else {}
    last_outcome = await post_cdp_turn(
        thread_id=thread_id,
        to_agent=to_agent,
        subject=subject,
        body=body,
        request_id=request_id,
        pointer_turn=pointer_turn,
        **post_extra,
    )
    if last_outcome.ok:
        return True
    await asyncio.sleep(_POST_RETRY_SLEEP_S)
    last_outcome = await post_cdp_turn(
        thread_id=thread_id,
        to_agent=to_agent,
        subject=subject,
        body=body,
        request_id=request_id,
        pointer_turn=pointer_turn,
        **post_extra,
    )
    if last_outcome.ok:
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
    last_outcome = final
    if not final.ok:
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
            http_status=last_outcome.http_status,
            detail_preview=last_outcome.detail_preview,
            archive_uri=result.archive_uri,
        )
    return final.ok
