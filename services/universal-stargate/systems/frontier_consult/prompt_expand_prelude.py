"""Run prompt-expand before a 10479/enrolled seat generate fires.

HTTP ``team_dispatch`` still returns 202 immediately. CDP already has a
background worker; cursor-sdk schedules expand+GIW on that same pattern.
Self-HTTP uses ``/v1/chat/completions`` (the sync MCP run path) from a
worker thread so the admit loop is not blocked.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from implement_admission.closeout_helpers import cortex_files_root, workspaces_root
from implement_admission.prompt_expand_admit import (
    PIPELINE_ID,
    expand_options,
    should_expand,
)
from prompt_expand_consume.router import (
    ConsumeBranch,
    ConsumeDecision,
    derive_attended,
    route_consume,
    stamp_expand_provenance,
)
from transport_utils import DEFAULT_STARGATE_URL, make_sync_client
from universal_logging import get_logger

if TYPE_CHECKING:
    from .cursor_sdk_prepared_handle import PreparedCursorSdkHandle

logger = get_logger(__name__)

_EXPAND_TIMEOUT_S = 600.0
_SDK_EXPAND_TASKS: set[asyncio.Task[None]] = set()


@dataclass(frozen=True, slots=True)
class ExpandRun:
    """Outcome of one caller-door expand attempt."""

    ok: bool
    prompt: str
    execution_id: str | None = None
    error: str | None = None


def read_prompt_uri(uri: str) -> str:
    """Load staged cortex:// or checkout-relative prompt bytes."""
    raw = (uri or "").strip()
    if raw.startswith("cortex://"):
        path = cortex_files_root() / raw[len("cortex://") :]
        return path.read_text(encoding="utf-8")
    path = Path(raw)
    if path.is_file():
        return path.read_text(encoding="utf-8")
    ws = workspaces_root() / raw
    return ws.read_text(encoding="utf-8")


def read_packet_text(packet_path: str) -> str:
    """Load a workspaces-relative or absolute packet."""
    raw = (packet_path or "").strip()
    if raw.startswith("workspaces://"):
        rest = raw[len("workspaces://") :]
        _, _, rel = rest.partition("/")
        return (workspaces_root() / rel).read_text(encoding="utf-8")
    path = Path(raw)
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return (workspaces_root() / raw).read_text(encoding="utf-8")


def run_prompt_expand(task: str, options: dict[str, str]) -> ExpandRun:
    """POST prompt-expand on the local Stargate completions door."""
    body = {
        "model": PIPELINE_ID,
        "messages": [{"role": "user", "content": task}],
        "pipeline_options": options,
    }
    try:
        with make_sync_client(
            DEFAULT_STARGATE_URL, timeout=_EXPAND_TIMEOUT_S
        ) as client:
            resp = client.post("/v1/chat/completions", json=body)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:  # noqa: BLE001 — fail-open at the caller door
        logger.warning("prompt-expand prelude transport failed: %s", exc)
        return ExpandRun(ok=False, prompt=task, error=str(exc))
    choices = data.get("choices") or []
    content = ""
    if choices:
        content = str((choices[0].get("message") or {}).get("content") or "")
    exec_id = resp.headers.get("x-pipeline-execution-id") or data.get("id")
    if not content.strip():
        return ExpandRun(
            ok=False, prompt=task, execution_id=exec_id, error="empty"
        )
    exec_s = str(exec_id) if exec_id else None
    return ExpandRun(ok=True, prompt=content, execution_id=exec_s)


def maybe_expand_cdp_prompt(
    *,
    prompt_uri: str,
    execution_id: str,
    thread_id: str,
    parent_thread: str | None,
    caller_agent: str | None,
    contract: str,
    model_id: str | None = None,
) -> str:
    """Return a (possibly restaged) prompt_uri after an enrolled expand."""
    try:
        prompt = read_prompt_uri(prompt_uri)
    except OSError as exc:
        logger.warning("prompt-expand prelude could not read %s: %s", prompt_uri, exc)
        return prompt_uri
    decision = should_expand(
        prompt=prompt,
        contract=contract,
        caller_agent=caller_agent,
        parent_thread=parent_thread,
        dispatch_thread_id=thread_id,
    )
    if not decision.admit:
        return prompt_uri
    result = run_prompt_expand(
        prompt,
        expand_options(contract=contract, model=model_id),
    )
    if not result.ok:
        logger.warning(
            "prompt-expand prelude fail-open execution_id=%s err=%s",
            execution_id,
            result.error,
        )
        return prompt_uri
    from claude_bundles.cdp_model_endpoint_staging import stage_prompt_uri

    staged = stage_prompt_uri(execution_id=execution_id, prompt_text=result.prompt)
    logger.info(
        "prompt-expand prelude cdp house=%s expand=%s generate=%s",
        decision.root,
        result.execution_id,
        execution_id,
    )
    return staged.prompt_uri


def _sdk_prompt(handle: PreparedCursorSdkHandle) -> str:
    if handle.packet_path:
        try:
            return read_packet_text(handle.packet_path)
        except OSError:
            return handle.message or ""
    return handle.message or ""


def _consume_context_from_handle(
    handle: PreparedCursorSdkHandle,
    *,
    attended: bool | None = None,
    durable_session: bool | None = None,
    transcript_id: str | None = None,
    original_commission: str | None = None,
) -> tuple[bool, bool]:
    """Derive attended/durable_session when caller did not pass explicit flags."""
    if attended is None:
        attended = derive_attended(
            transcript_id=transcript_id,
            commission_or_packet=original_commission or _sdk_prompt(handle),
        )
    if durable_session is None:
        durable_session = handle.effective_bus_lifecycle == "persistent"
    return attended, durable_session


def _attach_consume_decision(
    handle: PreparedCursorSdkHandle,
    decision: ConsumeDecision,
) -> PreparedCursorSdkHandle:
    return replace(
        handle,
        consume_branch=decision.branch.value,
        consume_activation=decision.activation_header,
        consume_reason=decision.reason,
    )


def route_expand_consume_for_handle(
    handle: PreparedCursorSdkHandle,
    *,
    task_prime: str,
    original_commission: str,
    expand_execution_id: str | None = None,
    attended: bool | None = None,
    durable_session: bool | None = None,
    summoning_thread_id: str | None = None,
    transcript_id: str | None = None,
) -> PreparedCursorSdkHandle:
    """Stamp TASK′ provenance and attach consume branch to *handle*."""
    attended_flag, durable_flag = _consume_context_from_handle(
        handle,
        attended=attended,
        durable_session=durable_session,
        transcript_id=transcript_id,
        original_commission=original_commission,
    )
    stamped = stamp_expand_provenance(task_prime, expand_execution_id)
    decision = route_consume(
        task_prime=stamped,
        original_commission=original_commission,
        attended=attended_flag,
        durable_session=durable_flag,
        summoning_thread_id=summoning_thread_id or handle.thread_id,
        transcript_id=transcript_id,
    )
    if decision.branch is ConsumeBranch.CONDUCTOR_RECOMMEND:
        logger.info(
            "prompt-expand consume conductor_recommend execution_id=%s reason=%s",
            handle.execution_id,
            decision.reason,
        )
    return _attach_consume_decision(handle, decision)


def consume_admit_fields(handle: PreparedCursorSdkHandle) -> dict[str, Any]:
    """Serialize consume branch + activation envelope for HTTP admit payloads."""
    fields: dict[str, Any] = {}
    if handle.consume_branch:
        fields["consume_branch"] = handle.consume_branch
    if handle.consume_reason:
        fields["consume_reason"] = handle.consume_reason
    task_prime = handle.message
    if task_prime:
        fields["task_prime"] = task_prime
    if handle.consume_branch == ConsumeBranch.CONDUCTOR_RECOMMEND.value:
        fields["consume_advisory"] = True
    if handle.consume_activation:
        fields["activation"] = {
            "kind": handle.consume_activation.get(
                "X-ULG-Activation-Kind", "prompt_expand_consume"
            ),
            "headers": dict(handle.consume_activation),
        }
        thread = handle.consume_activation.get("X-ULG-Summoning-Thread")
        if thread:
            fields["activation"]["summoning_thread_id"] = thread
        transcript = handle.consume_activation.get("X-ULG-Transcript-Id")
        if transcript:
            fields["activation"]["transcript_id"] = transcript
    return fields


def sdk_should_expand(handle: PreparedCursorSdkHandle) -> bool:
    """True when a prepared SDK handle is an enrolled work admit."""
    prompt = _sdk_prompt(handle)
    decision = should_expand(
        prompt=prompt,
        contract=handle.handoff_contract,
        caller_agent=handle.caller_agent,
        parent_thread=handle.parent_dispatch_thread_id,
        dispatch_thread_id=handle.dispatch_thread_id or handle.thread_id,
        continuity_root_thread_id=handle.continuity_root_thread_id,
    )
    return decision.admit


def apply_expand_to_handle(
    handle: PreparedCursorSdkHandle,
    *,
    transcript_id: str | None = None,
) -> PreparedCursorSdkHandle:
    """Rewrite packet or message in place on a copy. Fail-open returns handle."""
    prompt = _sdk_prompt(handle)
    result = run_prompt_expand(
        prompt,
        expand_options(
            contract=handle.handoff_contract,
            seat=handle.role,
            model=handle.resolved_model,
        ),
    )
    if not result.ok:
        logger.warning(
            "prompt-expand prelude sdk fail-open execution_id=%s err=%s",
            handle.execution_id,
            result.error,
        )
        return handle
    task_prime = stamp_expand_provenance(result.prompt, result.execution_id)
    expanded = handle
    if handle.packet_path:
        dest = (
            workspaces_root()
            / "tmp"
            / "prompts"
            / f"prompt-expand-{handle.execution_id}.md"
        )
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(task_prime, encoding="utf-8")
        rel = f"tmp/prompts/prompt-expand-{handle.execution_id}.md"
        logger.info(
            "prompt-expand prelude sdk expand=%s generate=%s packet=%s",
            result.execution_id,
            handle.execution_id,
            rel,
        )
        expanded = replace(handle, packet_path=rel)
    else:
        logger.info(
            "prompt-expand prelude sdk expand=%s generate=%s message",
            result.execution_id,
            handle.execution_id,
        )
        expanded = replace(handle, message=task_prime)
    return route_expand_consume_for_handle(
        expanded,
        task_prime=task_prime,
        original_commission=prompt,
        expand_execution_id=result.execution_id,
        transcript_id=transcript_id,
    )


@dataclass(frozen=True, slots=True)
class ExpandConsumeAdmitResult:
    """Outcome of expand+consume routing at the Stargate admit boundary."""

    scheduled_background: bool = False
    deliver_handle: PreparedCursorSdkHandle | None = None


async def expand_consume_admit_path(
    handle: PreparedCursorSdkHandle,
    dispatch: Any,
    *,
    transcript_id: str | None = None,
) -> ExpandConsumeAdmitResult:
    """Expand synchronously; schedule SDK dispatch or return a delivery handle."""
    if not sdk_should_expand(handle):
        return ExpandConsumeAdmitResult()

    expanded = await asyncio.to_thread(
        apply_expand_to_handle, handle, transcript_id=transcript_id
    )
    if expanded.consume_branch == ConsumeBranch.SDK_BACKGROUND.value:

        async def _run() -> None:
            try:
                await dispatch(expanded)
            except Exception:
                logger.exception(
                    "prompt-expand prelude sdk dispatch failed execution_id=%s",
                    handle.execution_id,
                )

        task = asyncio.create_task(
            _run(), name=f"sdk-expand-{handle.execution_id[:8]}"
        )
        _SDK_EXPAND_TASKS.add(task)
        task.add_done_callback(_SDK_EXPAND_TASKS.discard)
        return ExpandConsumeAdmitResult(scheduled_background=True)

    return ExpandConsumeAdmitResult(deliver_handle=expanded)


def schedule_sdk_expand_and_dispatch(
    handle: PreparedCursorSdkHandle,
    dispatch: Any,
) -> bool:
    """Start expand+consume off the HTTP path. True when background work was queued."""
    if not sdk_should_expand(handle):
        return False

    async def _run() -> None:
        await expand_consume_admit_path(handle, dispatch)

    task = asyncio.create_task(_run(), name=f"sdk-expand-{handle.execution_id[:8]}")
    _SDK_EXPAND_TASKS.add(task)
    task.add_done_callback(_SDK_EXPAND_TASKS.discard)
    return True


__all__ = [
    "ExpandConsumeAdmitResult",
    "ExpandRun",
    "apply_expand_to_handle",
    "consume_admit_fields",
    "expand_consume_admit_path",
    "maybe_expand_cdp_prompt",
    "read_packet_text",
    "read_prompt_uri",
    "route_expand_consume_for_handle",
    "run_prompt_expand",
    "schedule_sdk_expand_and_dispatch",
    "sdk_should_expand",
]
