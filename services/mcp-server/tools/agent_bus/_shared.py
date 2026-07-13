"""Shared constants and relay error helpers for agent_bus dispatchers."""

from __future__ import annotations

import json
import os
from typing import Any

_FETCH_CONTEXT_CAP = max(1, int(os.getenv("MCP_AGENT_BUS_CONTEXT_CAP", "50")))
_VALID_TURN_STATUSES = ("open", "resolved", "superseded", "waiting")

# Common wrong keys → canonical accepted key. Surfaced as a "did you mean"
# hint on the unknown-argument gate so callers do not have to discover the
# rename by trial (friction 16615: thread_id→thread, agent→from_agent).
# Prefer ``from=`` on the wire; ``from_agent`` is the permanent alias.
_ARG_ALIASES: dict[str, str] = {
    "thread_id": "thread",
    "threadid": "thread",
    "agent": "from_agent",
    "from": "from_agent",
    "author": "from_agent",
    "message": "body",
    "msg": "body",
    "text": "body",
    "content": "body",
    "turn": "turn_number",
    "title": "subject",
}


def relay(service: str, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
    """Delegate to the package-level ``_relay`` so tests can patch ``tools.agent_bus._relay``."""
    import tools.agent_bus as pkg

    return pkg._relay(service, method, path, **kwargs)


def _relay_detail(result: dict[str, Any]) -> Any:
    """Extract FastAPI ``detail`` from a relay error envelope."""
    detail = result.get("detail")
    if detail is not None:
        return detail
    body = result.get("body")
    if isinstance(body, str):
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            return None
        if isinstance(parsed, dict):
            return parsed.get("detail")
    return None


def _format_agent_bus_error(result: dict[str, Any], *, op: str) -> str:
    """Turn relay failures into agent-actionable messages."""
    if result.get("status_code") == 422:
        detail = _relay_detail(result)
        if isinstance(detail, list):
            for err in detail:
                if not isinstance(err, dict):
                    continue
                loc = err.get("loc") or ()
                if any(part == "status" for part in loc):
                    allowed = ", ".join(_VALID_TURN_STATUSES)
                    return (
                        f"{op}: invalid status value — turn status must be one of: "
                        f"{allowed}. Thread status (active|blocked|waiting|closed) "
                        "belongs on update_thread, not reply/post."
                    )
    return f"agent-bus error: {result.get('error', 'unknown error')}"


def _unknown_arg_error(
    *, tool: str, unknown: list[str], accepted: set[str]
) -> dict[str, Any]:
    """Build the unsupported-argument rejection, with canonical-alias hints."""
    ordered = sorted(unknown)
    hints = [
        f"{k!r} → {_ARG_ALIASES[k]!r}"
        for k in ordered
        if _ARG_ALIASES.get(k) in accepted
    ]
    hint_suffix = f" Did you mean: {', '.join(hints)}." if hints else ""
    return {
        "error": (
            f"{tool}: unsupported argument(s): {', '.join(ordered)}. "
            f"Accepted: {sorted(accepted)}.{hint_suffix}"
        )
    }


def _unread_turns_remediation(detail: dict[str, Any]) -> str:
    """Build the actionable mark-read remediation for a 409 unread_turns_exist."""
    latest = detail.get("latest_turn_number")
    if latest is not None:
        return (
            "Remediation: mark blocking turns read first, then retry — use "
            f'mark_read(thread, through_turn={latest}, agent=<you>) or '
            "fetch_unread(to=<you>, mark_read=true) to clear all."
        )
    unread = detail.get("unread_turns") or []
    pairs = [
        (str(t.get("thread")), t.get("turn_number"))
        for t in unread
        if isinstance(t, dict) and t.get("turn_number") is not None
    ]
    if not pairs:
        return (
            "Remediation: mark the turns addressed to you read first — "
            "fetch_unread(to=<you>, mark_read=true), or mark_read(thread, "
            "turn_numbers=[...]) / mark_read(thread, through_turn=N, agent=<you>) "
            "— then retry."
        )
    return (
        "Remediation: mark blocking turns read first, then retry — use "
        "mark_read(thread, through_turn=<latest from 409>, agent=<you>) or "
        "fetch_unread(to=<you>, mark_read=true) to clear all."
    )


def _turn_already_acknowledged_remediation(detail: dict[str, Any]) -> str:
    """Build remediation for 409 turn_already_acknowledged on update/delete."""
    thread = detail.get("thread")
    turn_number = detail.get("turn_number")
    read_at = detail.get("read_at")
    read_hint = f" (read_at={read_at})" if read_at else ""
    if thread is not None and turn_number is not None:
        return (
            f"Turn {turn_number} in thread {thread} was marked read{read_hint}; "
            "in-place edits are blocked once read_at is set. "
            f'Remediation: post follow-up content with send(thread="{thread}", ...) '
            "or reply instead of update; read_at cannot be cleared."
        )
    return (
        "Turn was marked read; in-place edits are blocked once read_at is set. "
        "Remediation: post follow-up content with send/reply instead of update; "
        "read_at cannot be cleared."
    )


def _normalize_relay_detail(detail: Any) -> tuple[Any, str | None]:
    """Normalize relay detail into a dict + machine reason when possible."""
    if isinstance(detail, dict):
        return detail, detail.get("error")
    if isinstance(detail, str) and "already acknowledged" in detail.lower():
        normalized = {
            "error": "turn_already_acknowledged",
            "message": detail,
        }
        return normalized, "turn_already_acknowledged"
    return detail, None


def _structured_relay_error(
    result: dict[str, Any], *, op: str
) -> dict[str, Any] | None:
    """Preserve relay ``status_code`` and structured ``detail`` for MCP callers."""
    status_code = result.get("status_code")
    detail, reason = _normalize_relay_detail(_relay_detail(result))
    if status_code is None and detail is None:
        return None
    base_error = result.get("error", "request failed")
    message = f"{op}: {base_error}"
    if reason:
        message = f"{message} ({reason})"
    envelope: dict[str, Any] = {
        "error": message,
        "status_code": status_code,
        "reason": reason,
        "detail": detail,
    }
    if reason == "unread_turns_exist" and isinstance(detail, dict):
        remediation = _unread_turns_remediation(detail)
        envelope["error"] = f"{message}. {remediation}"
        envelope["remediation"] = remediation
    elif reason == "turn_already_acknowledged" and isinstance(detail, dict):
        remediation = _turn_already_acknowledged_remediation(detail)
        envelope["error"] = f"{message}. {remediation}"
        envelope["remediation"] = remediation
    return envelope


def _structured_sidecar_write_failed(
    result: dict[str, Any],
) -> dict[str, Any] | None:
    """Re-shape relay 503 sidecar_write_failed into an actionable MCP envelope."""
    if result.get("status_code") != 503:
        return None
    detail = _relay_detail(result)
    if not (isinstance(detail, dict) and detail.get("code") == "sidecar_write_failed"):
        return None
    data = detail.get("data") if isinstance(detail.get("data"), dict) else {}
    thread_id = data.get("thread_id")
    thread_hint = (
        f" Retry with send(thread={thread_id!r}, ...) after fixing the write path."
        if thread_id is not None
        else ""
    )
    return {
        "error": (
            "send: durable sidecar write failed; turn was not inserted."
            f"{thread_hint}"
        ),
        "reason": "sidecar_write_failed",
        "code": "sidecar_write_failed",
        "retryable": detail.get("retryable", True),
        "data": data,
    }


def _structured_body_too_large(
    result: dict[str, Any], *, op: str
) -> dict[str, Any] | None:
    """Re-shape a relay 413 detail into the legacy structured error envelope."""
    detail = result.get("detail")
    if not (isinstance(detail, dict) and detail.get("reason") == "body_too_large"):
        return None
    limit = detail.get("limit_chars")
    body_chars = detail.get("body_chars")
    return {
        "error": (
            f"{op}: turn body exceeds limit "
            f"({body_chars:,} chars, limit {limit:,}). "
            "Agent-bus convention: short briefing + Cortex sidecar markdown "
            "reference. Write long content with fs(sandbox='cortex', op='write') "
            "to notes/system/threads/<thread>-<subject>.md "
            "and reference it in a brief body. If inline long-form delivery is "
            "required for this recipient, retry with allow_long_body=true."
        ),
        "reason": "body_too_large",
        "limit_chars": limit,
        "body_chars": body_chars,
        "suggestion": detail.get("suggestion", "sidecar_markdown_or_allow_long_body"),
    }
