"""MCP → Stargate edge admit/await for ``cursor_glass`` wake/rename."""

from __future__ import annotations

import os
import uuid
from typing import Any

from transport_utils import DEFAULT_STARGATE_URL, make_sync_client

EDGE_GLASS_WAKE_RENAME_ENV = "EDGE_GLASS_WAKE_RENAME_ENABLED"
EDGE_OPS_ENABLED_ENV = "EDGE_OPS_ENABLED"
AWAIT_MAX_SECONDS = 30.0
CODE_EDGE_PENDING = "edge_pending"
CODE_EDGE_UNAVAILABLE = "edge_unavailable"
_BRIDGE_GRANT = "bridge"
_BRIDGE_AUTH_SOURCE = "mcp:cursor_bridge"
_ADMIT_PATH = "/api/v1/edge/ops"
_RESULT_PATH = "/api/v1/edge/ops/{op_id}/result"
_HTTP_TIMEOUT = AWAIT_MAX_SECONDS + 5.0

_TRUE = frozenset({"true", "1", "yes", "on"})


def edge_ops_enabled() -> bool:
    return os.getenv(EDGE_OPS_ENABLED_ENV, "").strip().lower() in _TRUE


def edge_glass_wake_rename_enabled() -> bool:
    """True when both Stargate edge ops and this slice flag are on."""
    if not edge_ops_enabled():
        return False
    return os.getenv(EDGE_GLASS_WAKE_RENAME_ENV, "").strip().lower() in _TRUE


def _clamp_await_seconds(timeout_s: float | None) -> float:
    if timeout_s is None:
        return AWAIT_MAX_SECONDS
    return max(0.0, min(float(timeout_s), AWAIT_MAX_SECONDS))


def _apply_edge_finish(op: str, envelope: dict[str, Any]) -> dict[str, Any]:
    """Ledger and bus work when the hub receives a completed edge core result."""
    if op == "wake_tab":
        from cursor_bridge.bridge_glass_relay import finish_wake_tab

        return finish_wake_tab(envelope)
    if op == "rename_tab":
        from cursor_bridge.bridge_glass_relay import finish_rename_tab

        thread = envelope.get("thread")
        if thread is None and isinstance(envelope.get("args"), dict):
            thread = envelope["args"].get("thread")
        return finish_rename_tab(envelope, thread=str(thread) if thread else None)
    return envelope


def admit_and_await_cursor_glass(
    *,
    op: str,
    args: dict[str, Any],
    idempotency_key: str | None = None,
    deadline_s: float | None = None,
    wait_s: float | None = None,
) -> dict[str, Any]:
    """POST admit then GET result (binding 3: ≤30 s)."""
    key = idempotency_key or fresh_idempotency_key()
    effective_deadline = (
        AWAIT_MAX_SECONDS if deadline_s is None else min(float(deadline_s), 900.0)
    )
    admit_body = {
        "kind": "cursor_glass",
        "op": op,
        "args": args,
        "idempotency_key": key,
        "deadline_s": effective_deadline,
    }
    budget = _clamp_await_seconds(wait_s)
    stargate_url = os.environ.get("STARGATE_URL", DEFAULT_STARGATE_URL).strip()
    op_id: str | None = None

    try:
        with make_sync_client(stargate_url, timeout=_HTTP_TIMEOUT) as client:
            admit = client.post(_ADMIT_PATH, json=admit_body)
            if admit.status_code == 503:
                payload = admit.json()
                err = (payload.get("error") or {}) if isinstance(payload, dict) else {}
                if err.get("code") == CODE_EDGE_UNAVAILABLE:
                    return {
                        "ok": False,
                        "code": CODE_EDGE_UNAVAILABLE,
                        "detail": err.get("message", ""),
                    }
            if admit.status_code >= 400:
                payload = admit.json() if admit.content else {}
                err = (payload.get("error") or {}) if isinstance(payload, dict) else {}
                return {
                    "ok": False,
                    "code": str(err.get("code") or "edge_admit_failed"),
                    "detail": str(err.get("message") or admit.text[:200]),
                }
            op_id = (admit.json() or {}).get("op_id")
            if not op_id:
                return {
                    "ok": False,
                    "code": "edge_admit_failed",
                    "detail": "missing op_id",
                }

            result = client.get(
                _RESULT_PATH.format(op_id=op_id),
                params={"wait": budget},
            )
            if result.status_code >= 400:
                payload = result.json() if result.content else {}
                err = (payload.get("error") or {}) if isinstance(payload, dict) else {}
                return {
                    "ok": False,
                    "code": str(err.get("code") or "edge_await_failed"),
                    "detail": str(err.get("message") or result.text[:200]),
                    "op_id": op_id,
                }
            envelope = result.json()
    except OSError as exc:
        return {"ok": False, "code": CODE_EDGE_UNAVAILABLE, "detail": str(exc)[:200]}

    if not isinstance(envelope, dict):
        return {"ok": False, "code": "edge_await_failed", "detail": "non-json result"}

    code = str(envelope.get("code") or "")
    if code == CODE_EDGE_PENDING:
        return {
            "ok": False,
            "code": CODE_EDGE_PENDING,
            "op_id": envelope.get("op_id", op_id),
            "detail": "edge op still pending",
        }
    if code in {"edge_op_failed", "edge_op_expired", "edge_op_unknown"}:
        return {
            "ok": False,
            "code": code,
            "op_id": envelope.get("op_id", op_id),
            "detail": str(envelope.get("detail") or ""),
            "status": envelope.get("status"),
        }
    if envelope.get("ok") is False and code:
        envelope.setdefault("op_id", op_id)
        return envelope

    thread = args.get("thread")
    if thread is not None:
        envelope["thread"] = thread
    finished = _apply_edge_finish(op, envelope)
    finished.setdefault("op_id", op_id)
    return finished


def bridge_edge_args(**fields: Any) -> dict[str, Any]:
    """Default grant fields for cursor_bridge edge admits."""
    out = dict(fields)
    out.setdefault("grant", _BRIDGE_GRANT)
    out.setdefault("authorization_source", _BRIDGE_AUTH_SOURCE)
    return out


def fresh_idempotency_key() -> str:
    """Per-call idempotency for MCP edge admits."""
    return uuid.uuid4().hex


__all__ = [
    "AWAIT_MAX_SECONDS",
    "EDGE_GLASS_WAKE_RENAME_ENV",
    "admit_and_await_cursor_glass",
    "bridge_edge_args",
    "edge_glass_wake_rename_enabled",
    "edge_ops_enabled",
    "fresh_idempotency_key",
]
