"""Functional settle executor — subscribe_events window, snapshot, op=run."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

import httpx
from event_store.client import subscribe_events
from implement_admission.settle_gate import (
    MEMBERSHIP_SIGNAL,
    SETTLE_CAP_S,
    SettleVerdict,
    StopReason,
    affected_pipeline_ids,
    expected_absent_pipeline_ids,
    fold_settle_window,
    judge_event_window,
    membership_ready,
)

SubscribeFactory = Callable[..., AsyncIterator[dict[str, Any]]]
RunOpRunFn = Callable[[Iterable[str]], Awaitable[list[str]]]

DEFAULT_QUERY_SOCK = "/tmp/universal-protocol/events-query.sock"


@dataclass(frozen=True)
class FunctionalSettleResult:
    verdict: SettleVerdict
    op_run_failures: tuple[str, ...]
    timed_out: bool
    events_seen: int
    stop_reason: StopReason


def membership_snapshot_from_event(
    event: Mapping[str, Any],
) -> tuple[list[str], list[str]] | None:
    if str(event.get("signal") or "") != MEMBERSHIP_SIGNAL:
        return None
    payload = event.get("payload") or {}
    gateway_ids = payload.get("gateway_ids")
    pipeline_ids = payload.get("pipeline_ids")
    if not isinstance(gateway_ids, list) or not isinstance(pipeline_ids, list):
        return None
    return (
        [str(item) for item in gateway_ids],
        [str(item) for item in pipeline_ids],
    )


async def latest_gateway_membership_snapshot(
    query_sock: str = DEFAULT_QUERY_SOCK,
    *,
    query_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> tuple[list[str], list[str]] | None:
    """Latest persisted ``federation.gateway.membership`` in Event Service.

    Manage and stargate are separate processes; the live proxy is not available
    here. The last recorded membership event is the pre-restart baseline.
    """

    def _default(body: dict[str, Any]) -> dict[str, Any]:
        from scripts.model_manager.ui.dispatch_monitor.ulg.event_query import post_query

        return post_query(body, sock=query_sock, timeout=5.0)

    post = query_fn or _default
    try:
        body = await asyncio.to_thread(
            post,
            {
                "type": "operation",
                "name": "signal-events",
                "params": {
                    "signal": MEMBERSHIP_SIGNAL,
                    "limit": 100,
                    "minutes": 60 * 24 * 30,
                },
            },
        )
    except Exception:
        return None
    rows = body.get("rows") if isinstance(body, dict) else None
    if not isinstance(rows, list) or not rows:
        return None
    best_seq = -1
    best: tuple[list[str], list[str]] | None = None
    for row in rows:
        if not isinstance(row, dict):
            continue
        seq = row.get("seq")
        if not isinstance(seq, int) or seq <= best_seq:
            continue
        snap = membership_snapshot_from_event(row)
        if snap is None:
            continue
        best_seq = seq
        best = snap
    return best


async def request_pre_restart_gateway_membership(
    *,
    base_url: str = "",
    client: httpx.AsyncClient | None = None,
    query_sock: str = DEFAULT_QUERY_SOCK,
    emit_in_process: Callable[[], Awaitable[tuple[list[str], list[str]] | None]]
    | None = None,
    query_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> tuple[list[str], list[str]] | None:
    """Pre-restart snapshot from the last Event Service membership event.

    *base_url* and *client* are unused. The authed admin POST is not this path.
    """
    del base_url, client
    if emit_in_process is not None:
        try:
            return await emit_in_process()
        except Exception:
            return None
    return await latest_gateway_membership_snapshot(
        query_sock, query_fn=query_fn
    )


async def capture_event_resume_from(
    query_sock: str,
    *,
    query_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> int | None:
    """Event Service ``MAX(seq)`` before restart. Replay is ``seq > resume_from``."""

    def _default(body: dict[str, Any]) -> dict[str, Any]:
        from scripts.model_manager.ui.dispatch_monitor.ulg.event_query import post_query

        return post_query(body, sock=query_sock, timeout=5.0)

    post = query_fn or _default
    try:
        body = await asyncio.to_thread(
            post,
            {
                "type": "sql",
                "sql": "SELECT MAX(seq) AS seq FROM events",
            },
        )
    except Exception:
        return None
    rows = body.get("rows") if isinstance(body, dict) else None
    if not isinstance(rows, list) or not rows:
        return None
    seq = rows[0].get("seq") if isinstance(rows[0], dict) else None
    if isinstance(seq, int):
        return seq
    return None


async def op_run_affected_pipelines(
    pipeline_ids: Iterable[str],
    *,
    base_url: str,
    client: httpx.AsyncClient | None = None,
) -> list[str]:
    """Dispatch affected pipelines (``op=run``) and collect failures."""
    ids = [str(pid) for pid in pipeline_ids]
    if not ids:
        return []
    own = client is None
    http = client or httpx.AsyncClient(timeout=120.0)
    failures: list[str] = []
    try:
        for pipeline_id in ids:
            try:
                resp = await http.post(
                    f"{base_url.rstrip('/')}/api/v1/pipelines/dispatch",
                    json={
                        "model": pipeline_id,
                        "messages": [{"role": "user", "content": "settle-smoke"}],
                        "max_tokens": 1,
                    },
                )
                if resp.status_code >= 400:
                    failures.append(pipeline_id)
            except httpx.HTTPError:
                failures.append(pipeline_id)
    finally:
        if own:
            await http.aclose()
    return failures


async def _collect_events_until(
    agen: AsyncIterator[dict[str, Any]],
    *,
    deadline_mono: float,
    snapshot_gateway_ids: Iterable[str] | None = None,
    snapshot_pipeline_ids: Iterable[str] | None = None,
    land_paths: Iterable[str] = (),
    land_deleted_paths: Iterable[str] = (),
    pipeline_sources: Mapping[str, Iterable[str]] | None = None,
    step_type_modules: Mapping[str, str] | None = None,
    pipeline_step_types: Mapping[str, Iterable[str]] | None = None,
) -> tuple[list[dict[str, Any]], StopReason]:
    """Stop on a ready membership, the cap, or the end of a finite iterator.

    A live ``subscribe_events`` socket never raises ``StopAsyncIteration``.
    An Event Service restart or a closed socket does, and that is not
    acceptance: the stop reason is ``stream_ended``, not a cap expiry.
    """
    events: list[dict[str, Any]] = []
    stop_reason: StopReason = "cap"
    try:
        while True:
            remaining = deadline_mono - time.monotonic()
            if remaining <= 0:
                stop_reason = "cap"
                break
            try:
                event = await asyncio.wait_for(agen.__anext__(), timeout=remaining)
            except TimeoutError:
                stop_reason = "cap"
                break
            except StopAsyncIteration:
                stop_reason = "stream_ended"
                break
            events.append(event)
            if membership_ready(
                fold_settle_window(events),
                snapshot_gateway_ids=snapshot_gateway_ids,
                snapshot_pipeline_ids=snapshot_pipeline_ids,
                land_paths=land_paths,
                land_deleted_paths=land_deleted_paths,
                pipeline_sources=pipeline_sources,
                step_type_modules=step_type_modules,
                pipeline_step_types=pipeline_step_types,
            ):
                stop_reason = "ready"
                break
    finally:
        aclose = getattr(agen, "aclose", None)
        if aclose is not None:
            await aclose()
    return events, stop_reason


async def wait_functional_settle(
    *,
    query_sock: str,
    resume_from: int | None,
    snapshot_gateway_ids: Iterable[str] | None,
    snapshot_pipeline_ids: Iterable[str] | None,
    land_paths: Iterable[str],
    land_deleted_paths: Iterable[str] = (),
    cap_s: float = SETTLE_CAP_S,
    subscribe_factory: SubscribeFactory | None = None,
    run_op_run: RunOpRunFn | None = None,
    pipeline_sources: Mapping[str, Iterable[str]] | None = None,
    step_type_modules: Mapping[str, str] | None = None,
    pipeline_step_types: Mapping[str, Iterable[str]] | None = None,
) -> FunctionalSettleResult:
    """Wait on Event Service membership with a single outer cap, then op=run."""
    factory = subscribe_factory or subscribe_events
    deadline = time.monotonic() + cap_s
    snap_gw = list(snapshot_gateway_ids or ())
    snap_pipes = list(snapshot_pipeline_ids or ())
    paths = tuple(land_paths)
    deleted = tuple(land_deleted_paths)

    agen = factory(
        query_sock,
        filter={"signal": "federation.*"},
        resume_from=resume_from,
    )
    events, stop_reason = await _collect_events_until(
        agen,
        deadline_mono=deadline,
        snapshot_gateway_ids=snap_gw,
        snapshot_pipeline_ids=snap_pipes,
        land_paths=paths,
        land_deleted_paths=deleted,
        pipeline_sources=pipeline_sources,
        step_type_modules=step_type_modules,
        pipeline_step_types=pipeline_step_types,
    )
    timed_out = stop_reason == "cap"
    window = fold_settle_window(events)

    sources = pipeline_sources or {}
    affected = affected_pipeline_ids(
        snap_pipes,
        paths,
        pipeline_sources=sources,
        step_type_modules=step_type_modules or {},
        pipeline_step_types=pipeline_step_types or {},
    )
    expected_absent = expected_absent_pipeline_ids(
        snap_pipes,
        deleted,
        pipeline_sources=sources,
    )
    present_pipes = set(window.post_pipeline_ids or ())
    missing_expected = {
        pipeline_id
        for pipeline_id in expected_absent
        if pipeline_id not in present_pipes
    }
    op_run_ids = affected - missing_expected
    op_failures: list[str] = []
    if stop_reason == "ready" and op_run_ids and run_op_run is not None:
        op_failures = list(await run_op_run(op_run_ids))

    verdict = judge_event_window(
        events,
        snapshot_gateway_ids=snap_gw or None,
        snapshot_pipeline_ids=snap_pipes or None,
        timed_out=timed_out,
        land_paths=paths,
        land_deleted_paths=deleted,
        pipeline_sources=pipeline_sources,
        step_type_modules=step_type_modules,
        pipeline_step_types=pipeline_step_types,
        op_run_failures=op_failures,
    )
    return FunctionalSettleResult(
        verdict=verdict,
        op_run_failures=tuple(op_failures),
        timed_out=timed_out,
        events_seen=len(events),
        stop_reason=stop_reason,
    )


__all__ = [
    "FunctionalSettleResult",
    "membership_snapshot_from_event",
    "latest_gateway_membership_snapshot",
    "op_run_affected_pipelines",
    "capture_event_resume_from",
    "request_pre_restart_gateway_membership",
    "wait_functional_settle",
]
