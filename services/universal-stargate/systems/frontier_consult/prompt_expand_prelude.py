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


def apply_expand_to_handle(handle: PreparedCursorSdkHandle) -> PreparedCursorSdkHandle:
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
    if handle.packet_path:
        dest = (
            workspaces_root()
            / "tmp"
            / "prompts"
            / f"prompt-expand-{handle.execution_id}.md"
        )
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(result.prompt, encoding="utf-8")
        rel = f"tmp/prompts/prompt-expand-{handle.execution_id}.md"
        logger.info(
            "prompt-expand prelude sdk expand=%s generate=%s packet=%s",
            result.execution_id,
            handle.execution_id,
            rel,
        )
        return replace(handle, packet_path=rel)
    logger.info(
        "prompt-expand prelude sdk expand=%s generate=%s message",
        result.execution_id,
        handle.execution_id,
    )
    return replace(handle, message=result.prompt)


def schedule_sdk_expand_and_dispatch(
    handle: PreparedCursorSdkHandle,
    dispatch: Any,
) -> bool:
    """Start expand+GIW off the HTTP path. Return True when scheduled."""
    if not sdk_should_expand(handle):
        return False

    async def _run() -> None:
        try:
            expanded = await asyncio.to_thread(apply_expand_to_handle, handle)
            await dispatch(expanded)
        except Exception:
            logger.exception(
                "prompt-expand prelude sdk dispatch failed execution_id=%s",
                handle.execution_id,
            )
            await dispatch(handle)

    task = asyncio.create_task(
        _run(), name=f"sdk-expand-{handle.execution_id[:8]}"
    )
    _SDK_EXPAND_TASKS.add(task)
    task.add_done_callback(_SDK_EXPAND_TASKS.discard)
    return True


__all__ = [
    "ExpandRun",
    "apply_expand_to_handle",
    "maybe_expand_cdp_prompt",
    "read_packet_text",
    "read_prompt_uri",
    "run_prompt_expand",
    "schedule_sdk_expand_and_dispatch",
    "sdk_should_expand",
]
