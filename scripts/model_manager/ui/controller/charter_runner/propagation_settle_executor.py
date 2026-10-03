"""Functional settle executor — subscribe_events window, snapshot, op=run."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

import httpx

from implement_admission.settle_gate import (
    MEMBERSHIP_SIGNAL,
    SETTLE_CAP_S,
    SettleVerdict,
    affected_pipeline_ids,
    judge_event_window,
)
from event_store.client import subscribe_events

SubscribeFactory = Callable[..., AsyncIterator[dict[str, Any]]]
RunOpRunFn = Callable[[Iterable[str]], Awaitable[list[str]]]

DEFAULT_QUERY_SOCK = "/tmp/universal-protocol/events-query.sock"


@dataclass(frozen=True)
class FunctionalSettleResult:
    verdict: SettleVerdict
    op_run_failures: tuple[str, ...]
    timed_out: bool
    events_seen: int


def membership_snapshot_from_event(event: Mapping[str, Any]) -> tuple[list[str], list[str]] | None:
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


async def request_pre_restart_gateway_membership(
    *,
    base_url: str,
    client: httpx.AsyncClient | None = None,
) -> tuple[list[str], list[str]] | None:
    """Supervised pre-restart request — stargate emits membership on the event bus."""
    url = f"{base_url.rstrip('/')}/api/v1/admin/emit-gateway-membership"
    own = client is None
    http = client or httpx.AsyncClient(timeout=5.0)
    try:
        resp = await http.post(url)
        if resp.status_code != 200:
            return None
        data = resp.json()
        if not isinstance(data, dict):
            return None
        gw = data.get("gateway_ids")
        pipes = data.get("pipeline_ids")
        if not isinstance(gw, list) or not isinstance(pipes, list):
            return None
        return ([str(x) for x in gw], [str(x) for x in pipes])
    except (httpx.HTTPError, ValueError, OSError):
        return None
    finally:
        if own:
            await http.aclose()


async def _collect_events_until(
    agen: AsyncIterator[dict[str, Any]],
    *,
    deadline_mono: float,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    while True:
        remaining = deadline_mono - time.monotonic()
        if remaining <= 0:
            break
        try:
            event = await asyncio.wait_for(agen.__anext__(), timeout=remaining)
        except asyncio.TimeoutError:
            break
        except StopAsyncIteration:
            break
        events.append(event)
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
    events = await _collect_events_until(agen, deadline_mono=deadline)
    timed_out = time.monotonic() >= deadline

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
    "request_pre_restart_gateway_membership",
    "wait_functional_settle",
]
