"""Continuity MCP tool — first-class consolidate-continuity dispatch.

Agents call ``continuity(op=consolidate|replay|status, …)`` instead of
hand-assembling ``pipeline_options`` or running ``tmp/consolidate_replay.py``.
Option building reuses ``agent_bus_store.continuity_consolidate_trigger``.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, Literal

import httpx
from agent_bus_store.continuity_consolidate_trigger import (
    CALLER_AGENT,
    PIPELINE_ID,
    build_consolidate_options,
    resolve_root,
)
from mcp_events import monotonic_now, record
from transport_utils import make_sync_client
from universal_logging import get_logger

from pipelines.continuity_consolidate.v1.handlers._cortex import (
    hub_entity_id,
    parse_watermark,
)
from tools._cortex_relay import cx
from tools._restart_probe import annotate_unreachable_error
from tools.pipeline import STARGATE_URL, _pipeline_result

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = get_logger(__name__)

_DISPATCH_TIMEOUT = 15.0


def _no_root_house_error(trigger_thread: str) -> dict[str, Any]:
    return {
        "error": {
            "code": "no_root_house",
            "message": (
                f"Thread {trigger_thread} has no role:root house "
                "(not root and no lane parent with role:root)."
            ),
        },
        "status_code": 422,
    }


def _prepare_consolidate(
    *,
    trigger_thread: str,
    turn: int,
    dry_run: bool = False,
    force: bool = False,
    model_ref_overrides: dict[str, str] | None = None,
) -> tuple[str, dict[str, Any]] | dict[str, Any]:
    """Return ``(root, options)`` or a structured error dict."""
    root = resolve_root(trigger_thread)
    if root is None:
        return _no_root_house_error(trigger_thread)

    options = build_consolidate_options(
        root=root, trigger_thread=trigger_thread, turn_number=turn
    )
    if options.get("trigger") is None:
        return {
            "error": {
                "code": "trigger_not_found",
                "message": f"Turn {turn} not found on thread {trigger_thread}",
            },
            "status_code": 422,
        }

    if dry_run:
        options["dry_run"] = True
    if force:
        options["force"] = True
    if model_ref_overrides:
        options["model_ref_overrides"] = model_ref_overrides
    return root, options


def _continuity_async_dispatch(
    options: dict[str, Any],
    *,
    dispatch_thread_id: str,
) -> dict[str, Any]:
    """POST ``/api/v1/pipelines/dispatch`` for consolidate-continuity."""
    t0 = monotonic_now()
    record("mcp.continuity.async.called", pipeline=PIPELINE_ID)

    root = options["root_thread"]
    trigger = options.get("trigger") or {}
    body: dict[str, Any] = {
        "model": PIPELINE_ID,
        "messages": [
            {
                "role": "user",
                "content": (
                    f"consolidate {root} after "
                    f"{trigger.get('thread')}#{trigger.get('turn')}"
                ),
            }
        ],
        "pipeline_options": options,
        "dispatch_thread_id": dispatch_thread_id,
        "caller_agent": CALLER_AGENT,
    }

    url = "/api/v1/pipelines/dispatch"
    stargate_url = os.environ.get("STARGATE_URL", STARGATE_URL)
    try:
        with make_sync_client(stargate_url, timeout=_DISPATCH_TIMEOUT) as client:
            resp = client.post(url, json=body)
        if resp.status_code >= 400:
            try:
                payload = resp.json()
            except ValueError:
                payload = {
                    "error": {
                        "code": f"http_{resp.status_code}",
                        "message": resp.text[:500],
                    }
                }
            record(
                "mcp.continuity.async.failed",
                pipeline=PIPELINE_ID,
                status_code=resp.status_code,
            )
            if isinstance(payload, dict):
                if resp.status_code == 422 and "error" not in payload:
                    payload.setdefault(
                        "error",
                        {"code": "http_422", "message": resp.text[:500]},
                    )
                payload.setdefault("status_code", resp.status_code)
                return payload
            return {"error": payload, "status_code": resp.status_code}

        data = resp.json()
        record(
            "mcp.continuity.async.dispatched",
            pipeline=PIPELINE_ID,
            execution_id=data.get("execution_id", ""),
            duration_s=round(monotonic_now() - t0, 3),
        )
        return data
    except httpx.ConnectError as exc:
        record("mcp.continuity.async.failed", pipeline=PIPELINE_ID, error="connect_error")
        return annotate_unreachable_error(
            code="stargate_unreachable",
            message=f"Stargate not reachable: {exc}",
            service="stargate",
        )
    except httpx.HTTPError as exc:
        record("mcp.continuity.async.failed", pipeline=PIPELINE_ID, error=str(exc))
        return {"error": {"code": "http_error", "message": str(exc)}}


def _continuity_status_watermark(root_thread: str) -> dict[str, Any]:
    """Read hub WATERMARK assertion for a continuity root house."""
    entity_id = hub_entity_id(root_thread)
    assertions_reply = cx(
        "POST",
        "/dispatch",
        {
            "tool": "assertions",
            "arguments": {
                "entity_id": entity_id,
                "superseded": False,
                "limit": 100,
                "intent": "full",
            },
        },
        dispatch_tool="assertions",
    )
    if "error" in assertions_reply:
        return {
            "root_thread": root_thread,
            "hub_entity_id": entity_id,
            "error": assertions_reply.get("error"),
            "status_code": assertions_reply.get("status_code"),
        }

    rows = assertions_reply.get("assertions") or assertions_reply.get("items") or []
    watermark = parse_watermark([r for r in rows if isinstance(r, dict)])
    return {
        "root_thread": root_thread,
        "hub_entity_id": entity_id,
        "watermark": watermark,
    }


def _continuity_consolidate_or_replay(
    *,
    trigger_thread: str,
    turn: int,
    dry_run: bool,
    force: bool,
    model_ref_overrides: dict[str, str] | None,
    dispatch_thread_id: str | None,
) -> dict[str, Any]:
    prepared = _prepare_consolidate(
        trigger_thread=trigger_thread,
        turn=turn,
        dry_run=dry_run,
        force=force,
        model_ref_overrides=model_ref_overrides,
    )
    if isinstance(prepared, dict):
        return prepared

    root, options = prepared
    dispatch_root = dispatch_thread_id or root
    result = _continuity_async_dispatch(options, dispatch_thread_id=dispatch_root)
    if "execution_id" in result:
        result.setdefault("root_thread", root)
        result.setdefault("trigger_thread", trigger_thread)
        result.setdefault("turn", turn)
    return result


def register_continuity_tools(mcp: FastMCP) -> None:
    """Register the unified ``continuity`` tool on the code MCP surface."""

    @mcp.tool(title="Continuity")
    def continuity(
        op: Literal["consolidate", "replay", "status"],
        trigger_thread: str | None = None,
        turn: int | None = None,
        dry_run: bool | None = None,
        force: bool | None = None,
        model_ref_overrides: dict[str, str] | None = None,
        dispatch_thread_id: str | None = None,
        execution_id: str | None = None,
        root_thread: str | None = None,
        wait_seconds: float = 0.0,
    ) -> dict[str, Any]:
        """Continuity consolidation — dispatch ``consolidate-continuity`` without CLI.

        Ops:

        - ``consolidate`` — resolve root house for ``trigger_thread``, build
          ``pipeline_options`` from the trigger turn, async-dispatch the
          production fold. Required: ``trigger_thread``, ``turn``.

        - ``replay`` — same path with ``force=true`` and ``dry_run`` (default
          ``true``). Optional: ``model_ref_overrides``, ``dispatch_thread_id``.
          Required: ``trigger_thread``, ``turn``.

        - ``status`` — given ``execution_id``, fetch pipeline tracker state via
          ``pipeline(op=result)``; given ``root_thread``, read hub WATERMARK
          from Cortex. Provide exactly one of ``execution_id`` or ``root_thread``.
        """
        if op in {"consolidate", "replay"}:
            if not trigger_thread or turn is None:
                return {
                    "error": {
                        "code": "missing_required",
                        "message": f"op={op} requires trigger_thread and turn",
                    }
                }
            if op == "replay":
                effective_dry_run = True if dry_run is None else dry_run
                effective_force = True if force is None else force
            else:
                effective_dry_run = bool(dry_run)
                effective_force = bool(force)
            return _continuity_consolidate_or_replay(
                trigger_thread=trigger_thread,
                turn=turn,
                dry_run=effective_dry_run,
                force=effective_force,
                model_ref_overrides=model_ref_overrides,
                dispatch_thread_id=dispatch_thread_id,
            )

        if op == "status":
            if execution_id and root_thread:
                return {
                    "error": {
                        "code": "ambiguous_status",
                        "message": "Provide execution_id or root_thread, not both",
                    }
                }
            if execution_id:
                return _pipeline_result(execution_id, wait_seconds)
            if root_thread:
                return _continuity_status_watermark(root_thread)
            return {
                "error": {
                    "code": "missing_required",
                    "message": "op=status requires execution_id or root_thread",
                }
            }

        return {
            "error": {
                "code": "unknown_op",
                "message": f"Unknown op: {op}. Valid: consolidate|replay|status",
            }
        }


__all__ = [
    "register_continuity_tools",
    "_prepare_consolidate",
    "_continuity_async_dispatch",
    "_continuity_status_watermark",
    "_no_root_house_error",
]
