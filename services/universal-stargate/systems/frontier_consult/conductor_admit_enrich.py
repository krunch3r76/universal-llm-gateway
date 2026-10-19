"""Enrich team-dispatch-admit plan with pure conductor render fields (no disk birth)."""

from __future__ import annotations

import asyncio
from functools import partial
from typing import Any

from implement_admission.conductor_materialize import (
    rematerialize_context_from_dispatch,
)

from .generate_wrap import prepare_conductor_packet
from .handoff import _workspaces_root
from .stargate_cortex_reader import StargateCortexReader

CONDUCTOR_PACKET_MAX_BYTES = 256 * 1024


def reject_oversized_conductor_packet(markdown: str, *, request_id: str) -> None:
    """422 before any GIW call when the packet exceeds the named cap."""
    if len(markdown.encode("utf-8")) <= CONDUCTOR_PACKET_MAX_BYTES:
        return
    from .admission import FrontierEndpointError

    raise FrontierEndpointError(
        request_id=request_id,
        field="conductor",
        reason=(
            f"conductor packet exceeds cap {CONDUCTOR_PACKET_MAX_BYTES} bytes "
            "before GIW admission"
        ),
        status_code=422,
        code="conductor_packet_too_large",
    )


async def enrich_conductor_admit_plan(
    *,
    body: Any,
    plan: dict[str, Any],
    request_id: str,
) -> dict[str, Any]:
    """Attach ``packet_markdown`` to the admit plan; fail closed when oversized."""
    if str(getattr(body, "job", "") or "").strip().lower() != "conductor":
        return plan
    if plan.get("packet_markdown"):
        return plan
    source_ref = getattr(body, "source_ref", None)
    if not source_ref or getattr(body, "packet_path", None):
        return plan
    hop_seq_raw = getattr(body, "hop_seq", None)
    hop_seq = int(hop_seq_raw) if isinstance(hop_seq_raw, int) else 0
    if hop_seq >= 1:
        from implement_admission.conductor_score_journal import read_tip
        from implement_admission.source_ref import todo_slug_from_ref

        slug = todo_slug_from_ref(str(source_ref))
        if read_tip(slug, files_root=_workspaces_root()) is None:
            from .admission import FrontierEndpointError

            raise FrontierEndpointError(
                request_id=request_id,
                field="scoreboard",
                reason="scoreboard tip required for conductor continuation hop",
                status_code=422,
                code="conductor_tip_required",
            )
    gen_opts = getattr(body, "generation_options", None) or {}
    raw_summon = gen_opts.get("summon_mode")
    dispatch_tid = getattr(body, "dispatch_thread_id", None)
    summoning_turn_count: int | None = None
    if dispatch_tid and str(dispatch_tid).strip():
        from .cursor_sdk_thread_reuse import probe_thread

        thread_payload = await probe_thread(str(dispatch_tid).strip())
        if thread_payload is not None:
            summoning_turn_count = int(thread_payload.get("turn_count") or 0)
    rematerialize = rematerialize_context_from_dispatch(
        hop_seq=getattr(body, "hop_seq", None),
        hop_from=getattr(body, "hop_from", None),
        dispatch_thread_id=(
            str(dispatch_tid).strip()
            if dispatch_tid and str(dispatch_tid).strip()
            else None
        ),
        generation_options=gen_opts,
    )
    loop = asyncio.get_running_loop()
    wrap = await loop.run_in_executor(
        None,
        partial(
            prepare_conductor_packet,
            request_id=request_id,
            source_ref=str(source_ref),
            caller_agent=getattr(body, "caller_agent", None),
            summon_text=getattr(body, "prompt", None),
            summon_mode=(str(raw_summon).strip() if raw_summon else None),
            cortex=StargateCortexReader(),
            workspaces_root=_workspaces_root(),
            summoning_thread_id=(
                str(dispatch_tid).strip()
                if dispatch_tid and str(dispatch_tid).strip()
                else None
            ),
            summoning_turn_count=summoning_turn_count,
            rematerialize=rematerialize,
        ),
    )
    markdown = wrap.packet_markdown or ""
    reject_oversized_conductor_packet(markdown, request_id=request_id)
    out = dict(plan)
    out["source_ref"] = str(source_ref)
    out["packet_markdown"] = markdown
    out["packet_sha256"] = wrap.packet_sha256
    out["packet_rel_path"] = wrap.packet_rel_path
    out["birth_scoreboard_markdown"] = wrap.birth_scoreboard_markdown
    out["hop_seq"] = getattr(body, "hop_seq", None) or 0
    return out
