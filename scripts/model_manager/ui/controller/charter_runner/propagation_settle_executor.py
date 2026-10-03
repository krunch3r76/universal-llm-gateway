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
    CATALOG_CHANGED,
    MEMBERSHIP_SIGNAL,
    SETTLE_CAP_S,
    SettleVerdict,
    affected_pipeline_ids,
    gateway_id_sets_equal,
    judge_event_window,
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


async def _emit_membership_in_process() -> tuple[list[str], list[str]] | None:
    """Call stargate's emit function in this process. No second HTTP route.

    ``get_auth_dependency`` rejects an unauthenticated manage POST. When the
    proxy is not initialized here, the snapshot is missing and the restart
    still proceeds.
    """
    from systems.proxy.dependencies import get_proxy
    from systems.proxy.stargate.runtime.component_factory.pipeline_registry_bootstrap import (
        emit_gateway_membership,
        membership_payload,
    )

    proxy = get_proxy()
    payload = membership_payload(proxy)
    await emit_gateway_membership(proxy)
    gw = payload.get("gateway_ids")
    pipes = payload.get("pipeline_ids")
    if not isinstance(gw, list) or not isinstance(pipes, list):
        return None
    return ([str(x) for x in gw], [str(x) for x in pipes])


async def request_pre_restart_gateway_membership(
    *,
    base_url: str = "",
    client: httpx.AsyncClient | None = None,
    emit_in_process: Callable[[], Awaitable[tuple[list[str], list[str]] | None]]
    | None = None,
) -> tuple[list[str], list[str]] | None:
    """Supervised pre-restart snapshot via in-process ``emit_gateway_membership``.

    *base_url* and *client* are unused. The authed admin POST is not this path.
    """
    del base_url, client
    emit = emit_in_process or _emit_membership_in_process
    try:
        return await emit()
    except Exception:
        return None


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


def _membership_window_ready(
    events: Iterable[dict[str, Any]],
    snapshot_gateway_ids: Iterable[str] | None,
) -> bool:
    """True when a post-restart membership set matches and follows any catalog event."""
    snap = list(snapshot_gateway_ids or ())
    if not snap:
        return False
    post: list[str] | None = None
    membership_seq: int | None = None
    latest_catalog: int | None = None
    for event in events:
        signal = str(event.get("signal") or "")
        payload = event.get("payload") or {}
        seq = event.get("seq")
        if signal == CATALOG_CHANGED and isinstance(seq, int):
            latest_catalog = seq
        elif signal == MEMBERSHIP_SIGNAL:
            ids = payload.get("gateway_ids")
            if isinstance(ids, list):
                post = [str(item) for item in ids]
            if isinstance(seq, int):
                membership_seq = seq
    if post is None or not gateway_id_sets_equal(snap, post):
        return False
    if latest_catalog is not None and (
        membership_seq is None or membership_seq <= latest_catalog
    ):
        return False
    return True


async def _collect_events_until(
    agen: AsyncIterator[dict[str, Any]],
    *,
    deadline_mono: float,
    snapshot_gateway_ids: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """Stop on a matching membership event, the cap, or a finite iterator.

    A live ``subscribe_events`` socket never raises ``StopAsyncIteration``.
    Closing the iterator on match is what ends that subscription.
    """
    events: list[dict[str, Any]] = []
    try:
        while True:
            remaining = deadline_mono - time.monotonic()
            if remaining <= 0:
                break
            try:
                event = await asyncio.wait_for(agen.__anext__(), timeout=remaining)
            except TimeoutError:
                break
            except StopAsyncIteration:
                break
            events.append(event)
            if _membership_window_ready(events, snapshot_gateway_ids):
                break
    finally:
        aclose = getattr(agen, "aclose", None)
        if aclose is not None:
            await aclose()
    return events


async def wait_functional_settle(
    *,
    query_sock: str,
    resume_from: int | None,
    snapshot_gateway_ids: Iterable[str] | None,
    snapshot_pipeline_ids: Iterable[str] | None,
    land_paths: Iterable[str],
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

    agen = factory(
        query_sock,
        filter={"signal": "federation.*"},
        resume_from=resume_from,
    )
    events = await _collect_events_until(
        agen,
        deadline_mono=deadline,
        snapshot_gateway_ids=snap_gw,
    )
    timed_out = not _membership_window_ready(events, snap_gw) and (
        time.monotonic() >= deadline
    )

    affected = affected_pipeline_ids(
        snap_pipes,
        paths,
        pipeline_sources=pipeline_sources or {},
        step_type_modules=step_type_modules or {},
        pipeline_step_types=pipeline_step_types or {},
    )
    op_failures: list[str] = []
    if not timed_out and affected and run_op_run is not None:
        op_failures = list(await run_op_run(affected))

    verdict = judge_event_window(
        events,
        snapshot_gateway_ids=snap_gw or None,
        snapshot_pipeline_ids=snap_pipes or None,
        timed_out=timed_out,
        land_paths=paths,
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
    )


__all__ = [
    "FunctionalSettleResult",
    "membership_snapshot_from_event",
    "op_run_affected_pipelines",
    "capture_event_resume_from",
    "request_pre_restart_gateway_membership",
    "wait_functional_settle",
]
