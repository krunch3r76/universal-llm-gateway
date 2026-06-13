"""Cursor SDK dispatch route — admits dispatches via cursor-sdk-bridge.

In-memory idempotency registry state is lost on worker restart (Phase 1 scope).

Phase 2 HOME isolation (T2b 2026-06-11, thread 1559): each dispatch seeds a
private HOME with copied ``cli-config.json`` (identity), XDG ``auth.json``
(credential), and user-layer Cursor settings for ``setting_sources=all``.
``Client.launch_bridge`` snapshots ``os.environ`` at ``Popen`` (no ``env=``
kwarg in cursor-sdk 0.1.7), so HOME override uses a process-global swap
guarded by ``_SDK_DISPATCH_LOCK`` for the launch→wait→close window.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
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
    setup_cursor_dispatch_home,
)
from services.git_integration_worker.cursor_models import (
    build_model_selection,
    resolve_cursor,
)
from services.git_integration_worker.cursor_sdk_closeout import (
    SdkRunOutcome,
    count_tool_calls,
    degraded_implement_reason,
    format_closeout_body,
    infer_contract_from_text,
    resolve_prompt_preamble,
)
from services.git_integration_worker.cursor_sdk_context import (
    CursorSdkParityError,
    build_agent_options,
    validate_dispatch_context,
)
from services.git_integration_worker.cursor_sdk_events import (
    emit_sdk_worker_completed,
    emit_sdk_worker_failed,
    emit_sdk_worker_progress,
    emit_sdk_worker_timeout,
)
from services.git_integration_worker.cursor_sdk_gate import sdk_dispatch_slot
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
_SDK_DISPATCH_LOCK = threading.Lock()


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


@contextmanager
def _isolated_dispatch_home(home: Path):
    with _SDK_DISPATCH_LOCK:
        prev = os.environ.get("HOME")
        os.environ["HOME"] = str(home)
        try:
            yield
        finally:
            if prev is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = prev


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
    prompt: str,
    config_model_id: str,
    selection_overrides: dict[str, str] | None,
    dispatch_id: str,
    thread_id: str,
    resolved_model: str,
) -> SdkRunOutcome:
    dispatch_home = setup_cursor_dispatch_home(dispatch_id)
    bridge_state = dispatch_home / "bridge-state"
    bridge_state.mkdir(parents=True, exist_ok=True)
    CursorDispatchLedger.instance().record_state_root(
        dispatch_id=dispatch_id, state_root=str(bridge_state)
    )

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
    agent_options = build_agent_options(source_repo, selection)

    with _isolated_dispatch_home(dispatch_home):
        hb_thread, hb_stop = _start_heartbeat(
            dispatch_id=dispatch_id, thread_id=thread_id, resolved_model=resolved_model
        )
        client = Client.launch_bridge(
            _SDK_BRIDGE_BIN,
            workspace=str(source_repo),
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


async def _run_sdk_dispatch(
    *,
    req: CursorDispatchRequest,
    source_repo: Path,
    bus: CursorBusClient,
) -> None:
    async with sdk_dispatch_slot(dispatch_id=req.dispatch_id):
        await _run_sdk_dispatch_gated(req=req, source_repo=source_repo, bus=bus)


async def _run_sdk_dispatch_gated(
    *,
    req: CursorDispatchRequest,
    source_repo: Path,
    bus: CursorBusClient,
) -> None:
    outer_timeout_s = _SDK_TIMEOUT_S + _SDK_TIMEOUT_BUFFER_S
    try:
        prompt = _resolve_prompt(req, source_repo)
        try:
            outcome = await asyncio.wait_for(
                asyncio.to_thread(
                    _run_sdk_sync,
                    source_repo=source_repo,
                    prompt=prompt,
                    config_model_id=req.model,
                    selection_overrides=req.model_knobs,
                    dispatch_id=req.dispatch_id,
                    thread_id=req.thread_id,
                    resolved_model=req.model,
                ),
                timeout=outer_timeout_s,
            )
        except TimeoutError:
            emit_sdk_worker_timeout(
                dispatch_id=req.dispatch_id,
                thread_id=req.thread_id,
                resolved_model=req.model,
                timeout_s=outer_timeout_s,
            )
            env = error_envelope(
                code="CURSOR_SDK_TIMEOUT",
                message=(
                    f"cursor-sdk dispatch exceeded outer timeout ({outer_timeout_s:.0f}s)"
                ),
                source="gateway",
            )
            await bus.reply(
                thread_id=req.thread_id,
                to_agent="dispatch",
                from_agent="cursor-sdk",
                subject=f"cursor-sdk dispatch {req.dispatch_id} FAILED (timeout)",
                body=f"```json\n{json.dumps(env, indent=2)}\n```",
            )
            await _terminate_link(
                bus, thread_id=req.thread_id, terminal_status="failed"
            )
            await asyncio.to_thread(
                CursorDispatchLedger.instance().mark_terminal,
                dispatch_id=req.dispatch_id,
                terminal_status="failed",
            )
            return
        inferred_contract = None
        if not req.handoff_contract and not req.message and req.packet_path:
            inferred_contract = infer_contract_from_text(
                _read_packet_text(req, source_repo)
            )
        contract = (req.handoff_contract or inferred_contract or "consult").lower()
        degraded_reason = (
            degraded_implement_reason(outcome) if contract == "implement" else None
        )
        outcome_label = "degraded" if degraded_reason else "ok"
        body = format_closeout_body(outcome, degraded_reason)
        emit_sdk_worker_completed(
            dispatch_id=req.dispatch_id,
            thread_id=req.thread_id,
            duration_s=outcome.duration_ms / 1000.0,
            tool_call_count=outcome.tool_call_count,
            result_bytes=len(outcome.body.encode("utf-8")),
            outcome=outcome_label,
        )
        bus_result = await bus.reply(
            thread_id=req.thread_id,
            to_agent="dispatch",
            from_agent="cursor-sdk",
            subject=f"cursor-sdk dispatch {req.dispatch_id}",
            body=body,
        )
        if bus_result.status_code >= 400:
            logger.error(
                "cursor bus reply failed: status=%s body=%s",
                bus_result.status_code,
                bus_result.body,
            )
        await _terminate_link(bus, thread_id=req.thread_id, terminal_status="completed")
        await asyncio.to_thread(
            CursorDispatchLedger.instance().mark_terminal,
            dispatch_id=req.dispatch_id,
            terminal_status="completed",
        )
    except CursorHomeConfigError as exc:
        logger.error(
            "cursor sdk home/auth config failed: dispatch_id=%s err=%s",
            req.dispatch_id,
            exc,
        )
        env = error_envelope(
            code="CURSOR_HOME_CONFIG",
            message=str(exc),
            source="gateway",
        )
        await bus.reply(
            thread_id=req.thread_id,
            to_agent="dispatch",
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
    except Exception as exc:
        logger.exception("cursor sdk dispatch failed: dispatch_id=%s", req.dispatch_id)
        emit_sdk_worker_failed(
            dispatch_id=req.dispatch_id,
            thread_id=req.thread_id,
            error=str(exc),
        )
        env = error_envelope(
            code="CURSOR_SDK_DISPATCH",
            message=str(exc),
            source="gateway",
        )
        await bus.reply(
            thread_id=req.thread_id,
            to_agent="dispatch",
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
            execution_id=getattr(req, "execution_id", None),
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
        _run_sdk_dispatch(req=req, source_repo=cfg.source_repo, bus=bus)
    )
    ledger.register_task(req.dispatch_id, task)
    await asyncio.to_thread(ledger.mark_running, dispatch_id=req.dispatch_id)
    return admission
