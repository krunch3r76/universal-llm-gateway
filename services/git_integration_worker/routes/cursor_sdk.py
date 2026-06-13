"""Cursor SDK dispatch route — admits dispatches via cursor-sdk-bridge.

In-memory idempotency registry state is lost on worker restart (Phase 1 scope).

Phase 2 HOME isolation (T2b 2026-06-11, thread 1559): each dispatch seeds a
private HOME with copied ``cli-config.json`` (identity), XDG ``auth.json``
(credential), and user-layer Cursor settings for ``setting_sources=all``.
``Client.launch_bridge`` snapshots ``os.environ`` at ``Popen`` (no ``env=``
kwarg in cursor-sdk 0.1.7). Each dispatch records its HOME/venv override in
thread-local storage; a monkeypatch on ``_bridge_subprocess_env`` overlays it
into the bridge subprocess env at ``Popen`` time. The override is confined to
the dispatch's own worker thread, so concurrent and timed-out (orphan)
dispatches never race on shared global state and no dispatch lock is needed.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from collections.abc import Mapping
from contextlib import contextmanager
from pathlib import Path
from threading import Event as _ThreadEvent
from threading import Thread

from cursor_sdk import Client
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from universal_logging import get_logger
from universal_protocol import error_envelope

from services.git_integration_worker.config import WorkerConfig, load_config
from services.git_integration_worker.cursor_bus import CursorBusClient
from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
    DispatchConflict,
)
from services.git_integration_worker.cursor_home import (
    CursorHomeConfigError,
    CursorVenvConfigError,
    resolve_repo_venv,
    setup_cursor_dispatch_home,
    validate_repo_venv,
)
from services.git_integration_worker.cursor_models import (
    build_model_selection,
    resolve_cursor,
)
from services.git_integration_worker.cursor_sdk_closeout import (
    SdkRunOutcome,
    _extract_turn_number,
    build_closeout_idempotency_key,
    count_tool_calls,
    degraded_implement_reason,
    emit_implement_closeout_trigger,
    extract_source_ref_from_packet,
    format_delivery_fallback_body,
    infer_contract_from_text,
    prepare_closeout_delivery,
    resolve_completion_outcome,
    resolve_prompt_preamble,
    resolve_run_outcome_label,
)
from services.git_integration_worker.cursor_sdk_context import (
    CursorSdkParityError,
    build_agent_options,
    validate_dispatch_context,
)
from services.git_integration_worker.cursor_sdk_events import (
    emit_sdk_worker_completed,
    emit_sdk_worker_delivery_failed,
    emit_sdk_worker_failed,
    emit_sdk_worker_progress,
    emit_sdk_worker_timeout,
)
from services.git_integration_worker.cursor_sdk_gate import (
    acquire_sdk_dispatch_slot,
    release_sdk_dispatch_slot_sync,
)
from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/cursor", tags=["cursor-sdk"])

_CONFIG: WorkerConfig = load_config()
_SDK_BRIDGE_BIN = os.environ.get("CURSOR_SDK_BRIDGE_BIN", "").strip() or None
_SDK_TIMEOUT_S = float(os.environ.get("CURSOR_SDK_TIMEOUT", "1800"))
_SDK_TIMEOUT_BUFFER_S = 120.0
_SDK_HEARTBEAT_S = float(os.environ.get("CURSOR_SDK_HEARTBEAT", "30"))


def _config(request: Request) -> WorkerConfig:
    return getattr(request.app.state, "worker_config", _CONFIG)


def _read_packet_text(req: CursorDispatchRequest, source_repo: Path) -> str:
    if req.message:
        return req.message
    if req.packet_path is None:
        raise ValueError("dispatch requires either message or packet_path")
    rel = req.packet_path.strip()
    if rel.startswith("/") or ".." in Path(rel).parts:
        raise ValueError(f"packet_path must be workspaces-relative: {rel!r}")
    packet = (source_repo / rel).resolve()
    if not packet.is_relative_to(source_repo.resolve()):
        raise ValueError(f"packet_path escapes source_repo: {rel!r}")
    if not packet.is_file():
        raise ValueError(f"packet_path not found: {rel!r}")
    return packet.read_text(encoding="utf-8")


def _resolve_prompt(req: CursorDispatchRequest, source_repo: Path) -> str:
    packet_text = _read_packet_text(req, source_repo)
    inferred_contract = None if req.message else infer_contract_from_text(packet_text)
    preamble = resolve_prompt_preamble(
        handoff_contract=req.handoff_contract,
        prompt_preamble=req.prompt_preamble,
        inferred_contract=inferred_contract,
    )
    return f"{preamble}{packet_text}"


_dispatch_env = threading.local()
_BRIDGE_ENV_PATCH_INSTALLED = False
_PATH_PREPEND_KEY = "__CURSOR_SDK_PATH_PREPEND__"


def _dispatch_env_overlay() -> dict[str, str] | None:
    return getattr(_dispatch_env, "overrides", None)


def _install_bridge_env_patch() -> None:
    """Overlay the per-dispatch HOME/venv onto the bridge subprocess env.

    cursor-sdk 0.1.7 ``Bridge.launch`` builds the subprocess env from
    ``_bridge_subprocess_env()`` (``dict(os.environ)`` + SDK setdefaults)
    synchronously on the caller thread before ``Popen``. We wrap that function
    so it overlays the calling thread's dispatch override (HOME, VIRTUAL_ENV,
    PATH-prepend) read from thread-local storage, without mutating
    process-global ``os.environ``. Idempotent; patches the sync module global
    and the async module's imported binding.
    """
    global _BRIDGE_ENV_PATCH_INSTALLED
    if _BRIDGE_ENV_PATCH_INSTALLED:
        return

    from cursor_sdk import _bridge as _sdk_bridge

    _orig_env = _sdk_bridge._bridge_subprocess_env

    def _bridge_subprocess_env_with_overlay() -> Mapping[str, str]:
        env = dict(_orig_env())
        overrides = _dispatch_env_overlay()
        if overrides:
            home = overrides.get("HOME")
            if home is not None:
                env["HOME"] = home
            venv = overrides.get("VIRTUAL_ENV")
            if venv is not None:
                env["VIRTUAL_ENV"] = venv
            prepend = overrides.get(_PATH_PREPEND_KEY)
            if prepend is not None:
                cur = env.get("PATH")
                env["PATH"] = f"{prepend}{os.pathsep}{cur}" if cur else prepend
        return env

    _sdk_bridge._bridge_subprocess_env = _bridge_subprocess_env_with_overlay
    try:
        from cursor_sdk import _async_bridge as _sdk_async_bridge

        _sdk_async_bridge._bridge_subprocess_env = _bridge_subprocess_env_with_overlay
    except Exception:  # async bridge optional; the worker uses the sync path
        logger.debug("cursor-sdk async bridge env patch skipped (module absent)")

    _BRIDGE_ENV_PATCH_INSTALLED = True
    logger.info(
        "cursor-sdk bridge subprocess-env patch installed "
        "(thread-confined HOME overlay; no os.environ mutation)"
    )


@contextmanager
def _dispatch_home_overlay(home: Path, *, repo_venv: Path | None = None):
    """Thread-confined HOME/venv overlay for one dispatch.

    Records the override in thread-local storage read by the patched
    ``_bridge_subprocess_env`` during ``Client.launch_bridge`` (same thread).
    No ``os.environ`` mutation and no lock: each dispatch runs in its own
    ``asyncio.to_thread`` worker thread, so overrides never collide and a
    timed-out orphan thread cannot leak HOME into a newly admitted dispatch.
    """
    overrides: dict[str, str] = {"HOME": str(home)}
    if repo_venv is not None:
        overrides["VIRTUAL_ENV"] = str(repo_venv)
        overrides[_PATH_PREPEND_KEY] = str(repo_venv / "bin")
    prev = getattr(_dispatch_env, "overrides", None)
    _dispatch_env.overrides = overrides
    try:
        yield
    finally:
        _dispatch_env.overrides = prev


_install_bridge_env_patch()


def _start_heartbeat(
    *, dispatch_id: str, thread_id: str, resolved_model: str
) -> tuple[Thread, _ThreadEvent]:
    stop = _ThreadEvent()
    started = time.monotonic()

    def _loop() -> None:
        while not stop.wait(_SDK_HEARTBEAT_S):
            elapsed = time.monotonic() - started
            try:
                emit_sdk_worker_progress(
                    dispatch_id=dispatch_id,
                    thread_id=thread_id,
                    resolved_model=resolved_model,
                    elapsed_s=elapsed,
                    tool_call_count=0,
                )
                CursorDispatchLedger.instance().bump_heartbeat(dispatch_id=dispatch_id)
            except Exception as exc:  # heartbeat must never kill the dispatch
                logger.warning(
                    "sdk heartbeat emit failed: dispatch_id=%s err=%s",
                    dispatch_id,
                    exc,
                )

    t = Thread(target=_loop, name=f"sdk-hb-{dispatch_id}", daemon=True)
    t.start()
    return t, stop


def _run_sdk_sync(
    *,
    source_repo: Path,
    dispatch_workspace: Path,
    prompt: str,
    config_model_id: str,
    selection_overrides: dict[str, str] | None,
    dispatch_id: str,
    thread_id: str,
    resolved_model: str,
    gate_loop: asyncio.AbstractEventLoop,
) -> SdkRunOutcome:
    dispatch_home = setup_cursor_dispatch_home(dispatch_id)
    repo_venv = resolve_repo_venv()
    validate_repo_venv(repo_venv)
    bridge_state = dispatch_home / "bridge-state"
    bridge_state.mkdir(parents=True, exist_ok=True)
    CursorDispatchLedger.instance().record_state_root(
        dispatch_id=dispatch_id, state_root=str(bridge_state)
    )
    try:
        config = resolve_cursor(config_model_id)
        selection = build_model_selection(config, selection_overrides)
        parity = validate_dispatch_context(source_repo)
        knob_summary = {p.id: p.value for p in selection.params}
        logger.info(
            "cursor sdk dispatch start: dispatch_id=%s model=%s knobs=%s parity=%s",
            dispatch_id,
            config.model_id,
            knob_summary,
            parity,
        )
        agent_options = build_agent_options(source_repo, dispatch_workspace, selection)

        with _dispatch_home_overlay(dispatch_home, repo_venv=repo_venv):
            hb_thread, hb_stop = _start_heartbeat(
                dispatch_id=dispatch_id,
                thread_id=thread_id,
                resolved_model=resolved_model,
            )
            client = Client.launch_bridge(
                _SDK_BRIDGE_BIN,
                workspace=str(dispatch_workspace),
                state_root=str(bridge_state),
                timeout=_SDK_TIMEOUT_S,
                local=agent_options.local,
            )
            try:
                agent = client.create_agent(agent_options)
                # Local bridge Send rejects Idempotency-Key (cloud-only in SDK v1).
                run = agent.send(prompt)
                CursorDispatchLedger.instance().record_sdk_identity(
                    dispatch_id=dispatch_id,
                    agent_id=getattr(agent, "id", None),
                    run_id=getattr(run, "id", None),
                )
                result = run.wait()
                turns = run.conversation()
                return SdkRunOutcome(
                    body=result.result,
                    status=str(result.status),
                    duration_ms=result.duration_ms,
                    tool_call_count=count_tool_calls(turns),
                )
            finally:
                hb_stop.set()
                hb_thread.join(timeout=5.0)
                client.close()
    finally:
        # Release the capacity slot from this thread — not from the async
        # coroutine — so a timed-out orphan thread holds the slot until exit.
        release_sdk_dispatch_slot_sync(gate_loop)


async def _terminate_link(
    bus: CursorBusClient, *, thread_id: str, terminal_status: str
) -> None:
    result = await bus.terminate_dispatch(
        thread_id=thread_id, terminal_status=terminal_status
    )
    if result.status_code >= 400:
        logger.error(
            "cursor bus terminate failed: status=%s body=%s",
            result.status_code,
            result.body,
        )


async def _deliver_sdk_closeout(
    *,
    req: CursorDispatchRequest,
    source_repo: Path,
    outcome: SdkRunOutcome,
    degraded_reason: str | None,
    bus: CursorBusClient,
    reply_to: str,
    work_item_ref: str | None,
) -> None:
    delivery = prepare_closeout_delivery(
        source_repo=source_repo,
        dispatch_id=req.dispatch_id,
        outcome=outcome,
        degraded_reason=degraded_reason,
        thread_id=req.thread_id,
        work_item_ref=work_item_ref,
    )
    run_outcome = resolve_run_outcome_label(degraded_reason)
    duration_s = outcome.duration_ms / 1000.0

    bus_result = await bus.reply(
        thread_id=req.thread_id,
        to_agent=reply_to,
        from_agent="cursor-sdk",
        subject=f"cursor-sdk dispatch {req.dispatch_id}",
        body=delivery.body,
        allow_long_body=True,
    )

    if bus_result.status_code < 400:
        emit_sdk_worker_completed(
            dispatch_id=req.dispatch_id,
            thread_id=req.thread_id,
            execution_id=req.execution_id,
            duration_s=duration_s,
            tool_call_count=outcome.tool_call_count,
            result_bytes=delivery.full_result_bytes,
            outcome=resolve_completion_outcome(
                run_outcome=run_outcome, delivery_ok=True
            ),
        )
        turn_number = _extract_turn_number(bus_result.body)
        await emit_implement_closeout_trigger(
            body_json=delivery.body,
            source_ref=work_item_ref or delivery.sidecar_ref,
            idempotency_key=build_closeout_idempotency_key(
                execution_id=req.execution_id,
                thread_id=req.thread_id,
                turn_number=turn_number,
            ),
        )
        await _terminate_link(bus, thread_id=req.thread_id, terminal_status="completed")
        await asyncio.to_thread(
            CursorDispatchLedger.instance().mark_terminal,
            dispatch_id=req.dispatch_id,
            terminal_status="completed",
        )
        return

    logger.error(
        "cursor bus reply failed: status=%s body=%s",
        bus_result.status_code,
        bus_result.body,
    )
    emit_sdk_worker_delivery_failed(
        dispatch_id=req.dispatch_id,
        thread_id=req.thread_id,
        execution_id=req.execution_id,
        status_code=bus_result.status_code,
        result_bytes=delivery.full_result_bytes,
        sidecar_ref=delivery.sidecar_ref,
    )
    fallback_body = format_delivery_fallback_body(
        status_code=bus_result.status_code,
        sidecar_ref=delivery.sidecar_ref,
        result_bytes=delivery.full_result_bytes,
    )
    await bus.reply(
        thread_id=req.thread_id,
        to_agent=reply_to,
        from_agent="cursor-sdk",
        subject=f"cursor-sdk dispatch {req.dispatch_id} DELIVERY FAILED",
        body=fallback_body,
    )
    emit_sdk_worker_completed(
        dispatch_id=req.dispatch_id,
        thread_id=req.thread_id,
        execution_id=req.execution_id,
        duration_s=duration_s,
        tool_call_count=outcome.tool_call_count,
        result_bytes=delivery.full_result_bytes,
        outcome=resolve_completion_outcome(run_outcome=run_outcome, delivery_ok=False),
    )
    await _terminate_link(bus, thread_id=req.thread_id, terminal_status="failed")
    await asyncio.to_thread(
        CursorDispatchLedger.instance().mark_terminal,
        dispatch_id=req.dispatch_id,
        terminal_status="failed",
    )


async def _run_sdk_dispatch_gated(
    *,
    req: CursorDispatchRequest,
    source_repo: Path,
    dispatch_workspace: Path,
    bus: CursorBusClient,
) -> None:
    reply_to = req.caller_agent or "dispatch"
    outer_timeout_s = _SDK_TIMEOUT_S + _SDK_TIMEOUT_BUFFER_S
    gate_loop = asyncio.get_running_loop()

    # Acquire slot before spawning — released inside _run_sdk_sync finally block.
    await acquire_sdk_dispatch_slot(dispatch_id=req.dispatch_id)

    prompt = _resolve_prompt(req, source_repo)

    worker_task = asyncio.create_task(
        asyncio.to_thread(
            _run_sdk_sync,
            source_repo=source_repo,
            dispatch_workspace=dispatch_workspace,
            prompt=prompt,
            config_model_id=req.model,
            selection_overrides=req.model_knobs,
            dispatch_id=req.dispatch_id,
            thread_id=req.thread_id,
            resolved_model=req.model,
            gate_loop=gate_loop,
        )
    )

    done, _ = await asyncio.wait({worker_task}, timeout=outer_timeout_s)

    if not done:
        # Do NOT cancel worker_task. The thread is non-cancellable and owns the
        # gate slot until its finally block runs release_sdk_dispatch_slot_sync.
        worker_task.add_done_callback(
            lambda fut: logger.warning(
                "late cursor-sdk worker completed after timeout: dispatch_id=%s exc=%r",
                req.dispatch_id,
                fut.exception() if fut.done() and not fut.cancelled() else None,
            )
        )
        emit_sdk_worker_timeout(
            dispatch_id=req.dispatch_id,
            thread_id=req.thread_id,
            execution_id=req.execution_id,
            resolved_model=req.model,
            timeout_s=outer_timeout_s,
        )
        env = error_envelope(
            code="CURSOR_SDK_TIMEOUT",
            message=f"cursor-sdk dispatch exceeded outer timeout ({outer_timeout_s:.0f}s)",
            source="gateway",
        )
        await bus.reply(
            thread_id=req.thread_id,
            to_agent=reply_to,
            from_agent="cursor-sdk",
            subject=f"cursor-sdk dispatch {req.dispatch_id} FAILED (timeout)",
            body=f"```json\n{json.dumps(env, indent=2)}\n```",
        )
        await _terminate_link(bus, thread_id=req.thread_id, terminal_status="failed")
        await asyncio.to_thread(
            CursorDispatchLedger.instance().mark_terminal,
            dispatch_id=req.dispatch_id,
            terminal_status="failed",
        )
        return

    # Worker completed — slot already released by _run_sdk_sync finally block.
    try:
        outcome = worker_task.result()
    except CursorHomeConfigError as exc:
        logger.error(
            "cursor sdk home/auth config failed: dispatch_id=%s err=%s",
            req.dispatch_id,
            exc,
        )
        env = error_envelope(
            code="CURSOR_HOME_CONFIG", message=str(exc), source="gateway"
        )
        await bus.reply(
            thread_id=req.thread_id,
            to_agent=reply_to,
            from_agent="cursor-sdk",
            subject=f"cursor-sdk dispatch {req.dispatch_id} FAILED (home/auth)",
            body=f"```json\n{json.dumps(env, indent=2)}\n```",
        )
        await _terminate_link(bus, thread_id=req.thread_id, terminal_status="failed")
        await asyncio.to_thread(
            CursorDispatchLedger.instance().mark_terminal,
            dispatch_id=req.dispatch_id,
            terminal_status="failed",
        )
        return
    except CursorVenvConfigError as exc:
        logger.error(
            "cursor sdk venv config failed: dispatch_id=%s err=%s", req.dispatch_id, exc
        )
        env = error_envelope(
            code="CURSOR_VENV_CONFIG", message=str(exc), source="gateway"
        )
        await bus.reply(
            thread_id=req.thread_id,
            to_agent=reply_to,
            from_agent="cursor-sdk",
            subject=f"cursor-sdk dispatch {req.dispatch_id} FAILED (venv config)",
            body=f"```json\n{json.dumps(env, indent=2)}\n```",
        )
        await _terminate_link(bus, thread_id=req.thread_id, terminal_status="failed")
        await asyncio.to_thread(
            CursorDispatchLedger.instance().mark_terminal,
            dispatch_id=req.dispatch_id,
            terminal_status="failed",
        )
        return
    except Exception as exc:
        logger.exception("cursor sdk dispatch failed: dispatch_id=%s", req.dispatch_id)
        emit_sdk_worker_failed(
            dispatch_id=req.dispatch_id,
            thread_id=req.thread_id,
            execution_id=req.execution_id,
            error=str(exc),
        )
        env = error_envelope(
            code="CURSOR_SDK_DISPATCH", message=str(exc), source="gateway"
        )
        await bus.reply(
            thread_id=req.thread_id,
            to_agent=reply_to,
            from_agent="cursor-sdk",
            subject=f"cursor-sdk dispatch {req.dispatch_id} FAILED",
            body=f"```json\n{json.dumps(env, indent=2)}\n```",
        )
        await _terminate_link(bus, thread_id=req.thread_id, terminal_status="failed")
        await asyncio.to_thread(
            CursorDispatchLedger.instance().mark_terminal,
            dispatch_id=req.dispatch_id,
            terminal_status="failed",
        )
        return

    packet_text = _read_packet_text(req, source_repo) if req.packet_path else ""
    inferred_contract = (
        infer_contract_from_text(packet_text)
        if (not req.handoff_contract and not req.message and packet_text)
        else None
    )
    work_item_ref = extract_source_ref_from_packet(packet_text) if packet_text else None
    contract = (req.handoff_contract or inferred_contract or "consult").lower()
    degraded_reason = (
        degraded_implement_reason(outcome) if contract == "implement" else None
    )
    await _deliver_sdk_closeout(
        req=req,
        source_repo=source_repo,
        outcome=outcome,
        degraded_reason=degraded_reason,
        bus=bus,
        reply_to=reply_to,
        work_item_ref=work_item_ref,
    )


@router.post("/dispatch", response_model=CursorDispatchResponse)
async def cursor_dispatch(
    req: CursorDispatchRequest, request: Request
) -> CursorDispatchResponse:
    cfg = _config(request)
    try:
        config = resolve_cursor(req.model)
    except ValueError as exc:
        return JSONResponse(
            status_code=422,
            content=error_envelope(
                code="CURSOR_MODEL_UNTRUSTED",
                message=str(exc),
                source="gateway",
            ),
        )
    try:
        _resolve_prompt(req, cfg.source_repo)
    except ValueError as exc:
        return JSONResponse(
            status_code=422,
            content=error_envelope(
                code="CURSOR_PACKET_INVALID",
                message=str(exc),
                source="gateway",
            ),
        )
    try:
        parity = validate_dispatch_context(cfg.source_repo)
    except CursorSdkParityError as exc:
        return JSONResponse(
            status_code=422,
            content=error_envelope(
                code="CURSOR_SDK_PARITY",
                message=str(exc),
                source="gateway",
            ),
        )
    logger.info(
        "cursor sdk dispatch admitted: dispatch_id=%s thread_id=%s parity=%s",
        req.dispatch_id,
        req.thread_id,
        parity,
    )

    admission = CursorDispatchResponse(
        admitted=True,
        dispatch_id=req.dispatch_id,
        thread_id=req.thread_id,
        model_id=config.model_id,
    )
    ledger = CursorDispatchLedger.instance()
    fingerprint = ledger.fingerprint(req)
    try:
        cached = await asyncio.to_thread(
            ledger.admit,
            req=req,
            fingerprint=fingerprint,
            execution_id=req.execution_id,
            caller_agent=req.caller_agent,
            resolved_model=config.model_id,
            admission=admission,
        )
    except DispatchConflict as exc:
        return JSONResponse(
            status_code=409,
            content=error_envelope(
                code="CURSOR_DISPATCH_CONFLICT",
                message=str(exc),
                source="gateway",
            ),
        )
    if cached is not None:
        return cached

    bus = CursorBusClient()
    task = asyncio.create_task(
        _run_sdk_dispatch_gated(
            req=req,
            source_repo=cfg.source_repo,
            dispatch_workspace=cfg.dispatch_workspace,
            bus=bus,
        )
    )
    ledger.register_task(req.dispatch_id, task)
    await asyncio.to_thread(ledger.mark_running, dispatch_id=req.dispatch_id)
    return admission
