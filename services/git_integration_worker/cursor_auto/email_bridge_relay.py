"""Email-bridge UDS relay for tier-M ``execute`` jobs (life authority ops).

Fires ``email.pull`` and ``email.search`` through ``transport_utils`` against
``DEFAULT_EMAIL_BRIDGE_URL`` — not the MCP ``tools._local_relay`` package.
Registration is gated by :data:`EMAIL_BRIDGE_EXECUTE_RELAY_ENABLED` (default off);
enabling requires an operator DISPOSITION citing
``cortex://notes/system/specs/life-code-execute-bridge.md``.

v0 perimeter is socket reachability; op scoping is client-only at GIW
(manifest + registry). The email-bridge service itself does not refuse ops.
"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlencode

import httpx
from email_routing.surface_guard import (
    apply_indeterminate_if_degraded,
    bridge_status_fetcher,
    check_mailbox_surface,
)
from transport_utils import DEFAULT_EMAIL_BRIDGE_URL, make_sync_client

EMAIL_BRIDGE_EXECUTE_RELAY_FLAG = "EMAIL_BRIDGE_EXECUTE_RELAY_ENABLED"
DISPOSITION_BIND_URI = "cortex://notes/system/specs/life-code-execute-bridge.md"

_PULL_TIMEOUT = 120.0
_SEARCH_TIMEOUT = 30.0

_PULL_KEYS = frozenset({"mode", "mailbox", "folder", "to_filter"})
_SEARCH_KEYS = frozenset(
    {
        "sender",
        "to",
        "subject",
        "mailbox",
        "account",
        "date_from",
        "date_to",
        "ingested",
        "limit",
    }
)


class ExecuteRelayRefusalError(Exception):
    """Bridge or schema refusal — maps to a named execute closeout reason."""

    def __init__(self, reason: str, error: str) -> None:
        self.reason = reason
        self.error = error
        super().__init__(error)


def email_bridge_execute_relay_enabled() -> bool:
    """Return whether the production invoker may wire ``email.pull|search``."""
    raw = os.environ.get(EMAIL_BRIDGE_EXECUTE_RELAY_FLAG, "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def probe_email_bridge_status() -> dict[str, Any]:
    """Probe ``GET /status`` on email-bridge for pre-flip DISPOSITION (AC-0)."""
    try:
        return _request("GET", "/status", None, timeout=_SEARCH_TIMEOUT)
    except ExecuteRelayRefusalError as exc:
        return {"error": exc.error, "reason": exc.reason, "reachable": False}


def relay_email_pull(arguments: dict[str, Any]) -> dict[str, Any]:
    """Relay ``email.pull`` to ``POST /pull`` with mailbox guards."""
    _reject_unknown_keys(arguments, _PULL_KEYS)
    mode = arguments.get("mode")
    if not mode:
        raise ExecuteRelayRefusalError(
            "execute_args_schema_invalid",
            "email.pull requires tool_args.mode ('flagged' or 'folder')",
        )
    if mode not in ("flagged", "folder"):
        raise ExecuteRelayRefusalError(
            "execute_args_schema_invalid",
            f"mode must be 'flagged' or 'folder', got {mode!r}",
        )
    mailbox = str(arguments.get("mailbox") or "INBOX")
    guard = check_mailbox_surface(mailbox=mailbox)
    if guard is not None:
        raise ExecuteRelayRefusalError(
            "execute_relay_mailbox_guard",
            str(guard.get("message") or guard.get("error")),
        )
    body: dict[str, Any] = {"mode": mode, "mailbox": mailbox}
    if arguments.get("folder"):
        body["folder"] = arguments["folder"]
    if arguments.get("to_filter"):
        body["to_filter"] = arguments["to_filter"]
    status = _fetch_bridge_status()
    result = _request("POST", "/pull", body, timeout=_PULL_TIMEOUT)
    return apply_indeterminate_if_degraded(result, status=status)


def relay_email_search(arguments: dict[str, Any]) -> dict[str, Any]:
    """Relay ``email.search`` to ``GET /search`` with mailbox guards."""
    _reject_unknown_keys(arguments, _SEARCH_KEYS)
    mailbox = arguments.get("mailbox")
    account = arguments.get("account")
    guard = check_mailbox_surface(mailbox=mailbox, account=account)
    if guard is not None:
        raise ExecuteRelayRefusalError(
            "execute_relay_mailbox_guard",
            str(guard.get("message") or guard.get("error")),
        )
    params: dict[str, str | int] = {}
    for key in ("sender", "to", "subject", "mailbox", "account", "date_from", "date_to"):
        value = arguments.get(key)
        if value:
            params[key] = str(value)
    ingested = arguments.get("ingested")
    if ingested is not None:
        params["ingested"] = "true" if ingested else "false"
    params["limit"] = int(arguments.get("limit") or 50)
    path = f"/search?{urlencode(params)}"
    status = _fetch_bridge_status()
    result = _request("GET", path, None, timeout=_SEARCH_TIMEOUT)
    return apply_indeterminate_if_degraded(result, status=status)


def _reject_unknown_keys(arguments: dict[str, Any], allowed: frozenset[str]) -> None:
    unknown = sorted(set(arguments) - allowed)
    if unknown:
        raise ExecuteRelayRefusalError(
            "execute_args_schema_invalid",
            f"unknown argument keys: {unknown}",
        )


def _fetch_bridge_status() -> dict[str, Any]:
    try:
        return bridge_status_fetcher(
            lambda method, path, body=None: _request(
                method, path, body, timeout=_SEARCH_TIMEOUT
            )
        )
    except ExecuteRelayRefusalError:
        return {}


def _request(
    method: str,
    path: str,
    body: dict[str, Any] | None,
    *,
    timeout: float,
) -> dict[str, Any]:
    try:
        with make_sync_client(DEFAULT_EMAIL_BRIDGE_URL, timeout=timeout) as client:
            if method.upper() == "GET":
                response = client.get(path)
            else:
                response = client.post(path, json=body)
    except httpx.RequestError as exc:
        raise ExecuteRelayRefusalError(
            "execute_relay_unreachable",
            f"email-bridge connection failed: {exc}",
        ) from exc
    if response.status_code >= 400:
        raise ExecuteRelayRefusalError(
            "execute_relay_http_error",
            f"email-bridge HTTP {response.status_code}: {response.text[:200]}",
        )
    try:
        parsed = response.json()
    except ValueError as exc:
        raise ExecuteRelayRefusalError(
            "execute_relay_invalid_json",
            f"email-bridge returned invalid JSON: {response.text[:200]}",
        ) from exc
    if not isinstance(parsed, dict):
        raise ExecuteRelayRefusalError(
            "execute_relay_invalid_json",
            f"email-bridge returned {type(parsed).__name__}, expected object",
        )
    if "error" in parsed:
        raise ExecuteRelayRefusalError(
            "execute_relay_bridge_error",
            str(parsed.get("error")),
        )
    return parsed


__all__ = [
    "DISPOSITION_BIND_URI",
    "EMAIL_BRIDGE_EXECUTE_RELAY_FLAG",
    "ExecuteRelayRefusalError",
    "email_bridge_execute_relay_enabled",
    "probe_email_bridge_status",
    "relay_email_pull",
    "relay_email_search",
]
