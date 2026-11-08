"""Subscription mirror worker lane + safe frontier.worker_subscription_mirror emit."""

from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx
from transport_utils import DEFAULT_AGENT_BUS_URL, make_async_client
from universal_logging import get_logger

from .cursor_sdk_generate_signals import publish_frontier_event
from .cursor_sdk_lane_subscription import resolve_worker_subscription_agent_id
from .events import FrontierWorkerSubscriptionMirror

logger = get_logger(__name__)


def _bus_headers() -> dict[str, str]:
    token = os.getenv("AGENT_BUS_TOKEN", "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


def _bus_token_configured() -> bool:
    if os.getenv("AGENT_BUS_TOKEN", "").strip():
        return True
    return os.getenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _truncate_error(exc: BaseException) -> str:
    return repr(exc)[:500]


def publish_worker_subscription_mirror_event(
    *,
    coord_thread_id: str,
    worker_thread_id: str,
    serving_to_agent: str | None,
    outcome: str,
    bind_evidence: str | None,
    resolved_agent_id: str | None,
    eligible_agents: list[str],
    mirrored_agents: list[str],
    failed_agents: list[str],
    error: str | None,
) -> None:
    """Construct and emit mirror telemetry; never raises into the admit path."""
    try:
        publish_frontier_event(
            FrontierWorkerSubscriptionMirror(
                coord_thread_id=coord_thread_id,
                worker_thread_id=worker_thread_id,
                serving_to_agent=serving_to_agent,
                outcome=outcome,
                bind_evidence=bind_evidence,
                resolved_agent_id=resolved_agent_id,
                eligible_agents=eligible_agents,
                mirrored_agents=mirrored_agents,
                failed_agents=failed_agents,
                error=error,
            )
        )
    except Exception as exc:
        logger.warning(
            "worker_subscription_mirror event publish failed: "
            "coord=%s worker=%s err=%s",
            coord_thread_id,
            worker_thread_id,
            exc,
        )


async def mirror_coord_subscriptions_to_worker(
    *,
    coord_thread_id: str,
    worker_thread_id: str,
    serving_to_agent: str | None = None,
) -> None:
    """Copy active coord ``thread_scope`` subscriptions onto the worker lane (Rec 1)."""
    if not _bus_token_configured() or not coord_thread_id or not worker_thread_id:
        return
    if coord_thread_id == worker_thread_id:
        return

    outcome: str | None = None
    bind_evidence: str | None = None
    resolved_agent_id: str | None = None
    eligible_agents: list[str] = []
    mirrored_agents: list[str] = []
    failed_agents: list[str] = []
    error: str | None = None
    rows_read = False

    try:
        async with make_async_client(DEFAULT_AGENT_BUS_URL, timeout=10.0) as client:
            headers = _bus_headers()
            try:
                resp = await client.get(
                    "/endpoint-subscriptions",
                    params={"thread_scope": coord_thread_id},
                    headers=headers,
                )
                resp.raise_for_status()
                try:
                    payload = resp.json()
                except ValueError as exc:
                    outcome = "fetch_failed"
                    error = _truncate_error(exc)
                else:
                    rows = (
                        payload.get("subscriptions")
                        if isinstance(payload, dict)
                        else None
                    )
                    if not isinstance(rows, list):
                        outcome = "fetch_failed"
                        error = "subscriptions payload not a list"
                    else:
                        rows_read = True
                        eligible: list[dict[str, Any]] = []
                        for row in rows:
                            if not isinstance(row, dict):
                                continue
                            if str(row.get("state") or "") != "subscribed":
                                continue
                            agent = str(row.get("agent") or "").strip()
                            if not agent:
                                continue
                            eligible.append(row)
                            eligible_agents.append(agent)
                        if not eligible:
                            outcome = "no_rows"
                        else:
                            try:
                                resolved_aid, bind_evidence = (
                                    await resolve_worker_subscription_agent_id(
                                        client,
                                        worker_thread_id=worker_thread_id,
                                        serving_to_agent=serving_to_agent,
                                        headers=headers,
                                    )
                                )
                            except Exception as exc:
                                outcome = "skipped_unresolved"
                                bind_evidence = "unresolved:resolver_error"
                                resolved_agent_id = None
                                error = _truncate_error(exc)
                                logger.warning(
                                    "worker subscription resolver failed: coord=%s "
                                    "worker=%s err=%s",
                                    coord_thread_id,
                                    worker_thread_id,
                                    exc,
                                )
                            else:
                                resolved_agent_id = resolved_aid
                                if not resolved_aid:
                                    outcome = "skipped_unresolved"
                                else:
                                    first_post_error: str | None = None
                                    for row in eligible:
                                        agent = str(row.get("agent") or "").strip()
                                        body: dict[str, Any] = {
                                            "agent": agent,
                                            "thread_scope": worker_thread_id,
                                            "surface": str(row.get("surface") or ""),
                                            "display": str(row.get("display") or ""),
                                            "title": row.get("title"),
                                            "bound_by": "cursor-sdk-admit",
                                            "evidence": bind_evidence,
                                            "agent_id": resolved_aid,
                                        }
                                        try:
                                            sub_resp = await client.post(
                                                "/endpoint-subscriptions",
                                                json=body,
                                                headers=headers,
                                            )
                                            sub_resp.raise_for_status()
                                            mirrored_agents.append(agent)
                                        except httpx.HTTPError as exc:
                                            failed_agents.append(agent)
                                            if first_post_error is None:
                                                first_post_error = _truncate_error(exc)
                                            logger.warning(
                                                "worker-scoped subscribe failed: "
                                                "coord=%s worker=%s agent=%s err=%s",
                                                coord_thread_id,
                                                worker_thread_id,
                                                agent,
                                                exc,
                                            )
                                    if len(mirrored_agents) == len(eligible):
                                        outcome = "mirrored"
                                    elif not mirrored_agents:
                                        outcome = "failed"
                                        error = first_post_error
                                    else:
                                        outcome = "partial"
                                        error = first_post_error
            except httpx.HTTPError as exc:
                if outcome is None:
                    outcome = "fetch_failed" if not rows_read else "failed"
                    error = _truncate_error(exc)
                logger.warning(
                    "coord subscription mirror failed: coord=%s worker=%s err=%s",
                    coord_thread_id,
                    worker_thread_id,
                    exc,
                )
    except asyncio.CancelledError:
        if outcome is None:
            outcome = "failed" if rows_read else "fetch_failed"
        raise
    except Exception as exc:
        if outcome is None:
            outcome = "failed" if rows_read else "fetch_failed"
            error = _truncate_error(exc)
        logger.warning(
            "coord subscription mirror unexpected: coord=%s worker=%s err=%s",
            coord_thread_id,
            worker_thread_id,
            exc,
        )
    finally:
        if outcome is None:
            outcome = "failed"
            error = error or "mirror exited without outcome"
        publish_worker_subscription_mirror_event(
            coord_thread_id=coord_thread_id,
            worker_thread_id=worker_thread_id,
            serving_to_agent=serving_to_agent,
            outcome=outcome,
            bind_evidence=bind_evidence,
            resolved_agent_id=resolved_agent_id,
            eligible_agents=eligible_agents,
            mirrored_agents=mirrored_agents,
            failed_agents=failed_agents,
            error=error,
        )
