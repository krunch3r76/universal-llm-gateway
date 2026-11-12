"""MCP → Stargate edge admit/await for ``cursor_glass`` wake/rename."""

from __future__ import annotations

import hashlib
import os
import uuid
from typing import Any

from systems.edge_ops import config as edge_ops_config
from systems.edge_ops.model import CODE_EDGE_PENDING, CODE_EDGE_UNAVAILABLE
from transport_utils import DEFAULT_STARGATE_URL, make_sync_client

EDGE_GLASS_WAKE_RENAME_ENV = "EDGE_GLASS_WAKE_RENAME_ENABLED"
_BRIDGE_GRANT = "bridge"
_BRIDGE_AUTH_SOURCE = "mcp:cursor_bridge"
_ADMIT_PATH = "/api/v1/edge/ops"
_RESULT_PATH = "/api/v1/edge/ops/{op_id}/result"
_HTTP_TIMEOUT = edge_ops_config.AWAIT_MAX_SECONDS + 5.0

_TRUE = frozenset({"true", "1", "yes", "on"})


def edge_glass_wake_rename_enabled() -> bool:
    """True when both Stargate edge ops and this slice flag are on."""
    if not edge_ops_config.edge_ops_enabled():
        return False
    return os.getenv(EDGE_GLASS_WAKE_RENAME_ENV, "").strip().lower() in _TRUE


def _validate_bridge_grant(*, grant: str, authorization_source: str) -> dict[str, Any] | None:
    grant_s = grant.strip().lower()
    auth = authorization_source.strip()
    if grant_s not in {"bridge", "explicit"} or not auth:
        return {
            "ok": False,
            "error": "grant_or_source_required",
            "detail": "grant (bridge|explicit) and authorization_source are required",
        }
    return None


def _idempotency_key(op: str, args: dict[str, Any]) -> str:
    raw = f"{op}:{args.get('gui_host')}:{args.get('agent_id')}:{args.get('body') or args.get('title')}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def admit_and_await_cursor_glass(
    *,
    op: str,
    args: dict[str, Any],
    idempotency_key: str | None = None,
    deadline_s: float | None = None,
    wait_s: float | None = None,
) -> dict[str, Any]:
    """POST admit then GET result (binding 3: ≤30 s)."""
    grant_err = _validate_bridge_grant(
        grant=str(args.get("grant") or ""),
        authorization_source=str(args.get("authorization_source") or ""),
    )
    if grant_err is not None:
        return grant_err

    key = idempotency_key or _idempotency_key(op, args)
    admit_body = {
        "kind": "cursor_glass",
        "op": op,
        "args": args,
        "idempotency_key": key,
        "deadline_s": deadline_s,
    }
    budget = edge_ops_config.clamp_await_seconds(wait_s)
    stargate_url = os.environ.get("STARGATE_URL", DEFAULT_STARGATE_URL).strip()

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
                return {"ok": False, "code": "edge_admit_failed", "detail": "missing op_id"}

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
    envelope.pop("op_id", None)
    return envelope


def bridge_edge_args(**fields: Any) -> dict[str, Any]:
    """Default grant fields for cursor_bridge edge admits."""
    out = dict(fields)
    out.setdefault("grant", _BRIDGE_GRANT)
    out.setdefault("authorization_source", _BRIDGE_AUTH_SOURCE)
    return out


def fresh_idempotency_key() -> str:
    """Caller-supplied idempotency when args are not stable enough to hash."""
    return uuid.uuid4().hex


__all__ = [
    "EDGE_GLASS_WAKE_RENAME_ENV",
    "admit_and_await_cursor_glass",
    "bridge_edge_args",
    "edge_glass_wake_rename_enabled",
    "fresh_idempotency_key",
]
