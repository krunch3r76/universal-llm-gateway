"""SMS dispatch router — admits v0 ops; rejects unknown ops."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

V0_OPS: frozenset[str] = frozenset(
    {
        "list",
        "status",
        "threads",
        "read",
        "search",
        "capture",
        "extract",
        "draft_new",
        "draft_get",
        "draft_list",
        "draft_revise",
        "send",
        "send_status",
    }
)

CATALOG: dict[str, dict[str, str | int]] = {
    "list": {"tier": "R", "phase": 0, "status": "live", "desc": "Op catalog"},
    "status": {"tier": "R", "phase": 0, "status": "live", "desc": "Bridge health + gateway gauges"},
    "threads": {"tier": "R", "phase": 0, "status": "live", "desc": "Per-counterparty thread list"},
    "read": {"tier": "R", "phase": 0, "status": "live", "desc": "Thread-scoped message read"},
    "search": {"tier": "R", "phase": 0, "status": "live", "desc": "Text search over archive"},
    "capture": {"tier": "I", "phase": 0, "status": "live", "desc": "Archive JSONL/render only; no Cortex"},
    "extract": {"tier": "I", "phase": 0, "status": "live", "desc": "Explicit Cortex extraction (phase 2)"},
    "draft_new": {"tier": "D", "phase": 0, "status": "live", "desc": "Create outbound draft"},
    "draft_get": {"tier": "R", "phase": 0, "status": "live", "desc": "Get draft by id"},
    "draft_list": {"tier": "R", "phase": 0, "status": "live", "desc": "List drafts"},
    "draft_revise": {"tier": "D", "phase": 0, "status": "live", "desc": "Revise draft body/recipients"},
    "send": {"tier": "O", "phase": 0, "status": "gated", "desc": "Send approved draft (draft_id + dispatch_token only)"},
    "send_status": {"tier": "R", "phase": 0, "status": "live", "desc": "Outbound delivery status"},
}


def reject_unknown_op(op: str) -> dict[str, Any] | None:
    if op in V0_OPS:
        return None
    return {
        "error": f"Unknown sms op {op!r}. Admitted v0 ops: {sorted(V0_OPS)}",
        "available": sorted(V0_OPS),
    }


def op_list(**_: object) -> dict[str, Any]:
    live = [op for op, m in CATALOG.items() if m["status"] in ("live", "gated")]
    return {"live_ops": sorted(live), "total_ops": len(CATALOG), "catalog": CATALOG}


def build_handlers(relay: Callable[[str, str, dict[str, Any] | None], dict[str, Any]]) -> dict[str, Callable[..., dict[str, Any]]]:
    """Build op handlers that delegate to sms-bridge REST via relay helper."""

    def op_status(**_: object) -> dict[str, Any]:
        return relay("GET", "/status")

    def op_threads(**_: object) -> dict[str, Any]:
        return relay("GET", "/threads")

    def op_read(message_id: str | None = None, **_: object) -> dict[str, Any]:
        if not message_id:
            return {"error": "message_id is required"}
        return relay("GET", f"/messages/{message_id}")

    def op_search(q: str | None = None, limit: int = 20, **_: object) -> dict[str, Any]:
        if not q:
            return {"error": "q is required"}
        return relay("GET", f"/messages/search?q={q}&limit={limit}")

    def op_capture(messages: list[dict[str, Any]] | None = None, **_: object) -> dict[str, Any]:
        if not messages:
            return {"error": "messages (list) is required"}
        return relay("POST", "/capture", {"messages": messages})

    def op_extract(thread_id: str | None = None, **_: object) -> dict[str, Any]:
        if not thread_id:
            return {"error": "thread_id is required"}
        return relay("POST", "/extract", {"thread_id": thread_id})

    def op_draft_new(
        to_e164: str | None = None, body_text: str | None = None, **_: object
    ) -> dict[str, Any]:
        if not to_e164 or not body_text:
            return {"error": "to_e164 and body_text are required"}
        return relay("POST", "/drafts", {"to_e164": to_e164, "body_text": body_text})

    def op_draft_get(draft_id: str | None = None, **_: object) -> dict[str, Any]:
        if not draft_id:
            return {"error": "draft_id is required"}
        return relay("GET", f"/drafts/{draft_id}")

    def op_draft_list(**_: object) -> dict[str, Any]:
        return relay("GET", "/drafts")

    def op_draft_revise(
        draft_id: str | None = None, body_text: str | None = None, **_: object
    ) -> dict[str, Any]:
        if not draft_id or body_text is None:
            return {"error": "draft_id and body_text are required"}
        return relay("PATCH", f"/drafts/{draft_id}", {"body_text": body_text})

    def op_send(
        draft_id: str | None = None,
        dispatch_token: str | None = None,
        override: bool = False,
        approval_tier: str | None = None,
        **_: object,
    ) -> dict[str, Any]:
        if not draft_id or not dispatch_token:
            return {"error": "draft_id and dispatch_token are required — no body param on send"}
        body = {
            "dispatch_token": dispatch_token,
            "override": override,
        }
        if approval_tier:
            body["approval_tier"] = approval_tier
        return relay("POST", f"/drafts/{draft_id}/send", body)

    def op_send_status(draft_id: str | None = None, **_: object) -> dict[str, Any]:
        if not draft_id:
            return {"error": "draft_id is required"}
        return relay("GET", f"/drafts/{draft_id}")

    return {
        "list": op_list,
        "status": op_status,
        "threads": op_threads,
        "read": op_read,
        "search": op_search,
        "capture": op_capture,
        "extract": op_extract,
        "draft_new": op_draft_new,
        "draft_get": op_draft_get,
        "draft_list": op_draft_list,
        "draft_revise": op_draft_revise,
        "send": op_send,
        "send_status": op_send_status,
    }


def dispatch_sms(
    op: str,
    arguments: str,
    *,
    relay: Callable[[str, str, dict[str, Any] | None], dict[str, Any]],
) -> dict[str, Any]:
    """Route dispatch(tool=sms) — admit v0 ops, reject unknown."""
    rejection = reject_unknown_op(op)
    if rejection:
        return rejection
    try:
        args = json.loads(arguments)
        if not isinstance(args, dict):
            return {"error": f"arguments must be a JSON object, got {type(args).__name__}"}
    except json.JSONDecodeError as exc:
        return {"error": f"Invalid arguments JSON: {exc}"}

    handlers = build_handlers(relay)
    handler = handlers.get(op)
    if handler is None:
        return reject_unknown_op(op) or {"error": f"no handler for op {op!r}"}
    return handler(**args)
