"""Parse-time refusal for manage actions outside maestro-landed-live slice 1."""

from __future__ import annotations

from typing import Any

# Slice 1 read-only manage methods (AC3).
ALLOWED_MANAGE_METHODS = frozenset({"busy_status", "status", "health", "fleet_liveness"})

# AC7 + plan §0: refused always, including dry run.
_REFUSED_ACTIONS = frozenset(
    {
        "wait_for_boundary",
        "rebuild",
        "stop",
        "sync_restart",
        "wait_healthy",
        "cancel_restart_intent",
    }
)


def refuse_manage_call(method: str, params: dict[str, Any] | None = None) -> str | None:
    """Return a refusal reason, or None when the call is allowed for slice 1."""
    params = params or {}
    action = (params.get("action") or params.get("method") or "").strip()
    service = (params.get("service") or "").strip()

    if params.get("force") is True and service == "git_integration_worker":
        return "refused force on git_integration_worker"

    if method == "recycle_giw" and params.get("force") is not True:
        return None

    if method == "manage":
        if action in _REFUSED_ACTIONS:
            return f"refused manage action {action!r}"
        if action.startswith("charter_"):
            return f"refused manage action {action!r}"
        if action == "recycle_giw":
            if params.get("force") is True:
                return "refused recycle_giw with force"
            return None
        if action == "wait_for_boundary":
            return "refused wait_for_boundary"
        if action not in ALLOWED_MANAGE_METHODS and action:
            return f"refused manage action {action!r}"
        return None

    if method in _REFUSED_ACTIONS or method.startswith("charter_"):
        return f"refused method {method!r}"
    if method == "recycle_giw" and params.get("force") is True:
        return "refused recycle_giw with force"
    if method == "agent_bus":
        return "refused agent_bus send"
    if method not in ALLOWED_MANAGE_METHODS:
        return f"refused method {method!r}"
    return None


def trace_allowed(trace: list[dict[str, Any]]) -> bool:
    """True when every recorded manage call is slice-1 allowed (AC3)."""
    for entry in trace:
        method = entry.get("method", "")
        params = entry.get("params") if isinstance(entry.get("params"), dict) else {}
        if refuse_manage_call(str(method), params):
            return False
    return True
