"""Fail-closed mailbox→surface guards for the email-bridge MCP tool.

Slice 1 (agent-bus:5978 consult): typed outcomes at call time — not a routing op.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import yaml

_IMAP_BRIDGE_ID = "imap_bridge"
_M365_GRAPH_ID = "m365_graph"
_KNOWN_SURFACE_IDS = (_IMAP_BRIDGE_ID, _M365_GRAPH_ID)

_HEADLESS_EXPORT_INVOKE = (
    "scripts/email/headless_export.py with email-sync intent YAML (Graph export)"
)
_IMAP_INVOKE = 'dispatch(tool="email") for IMAP email-bridge mailboxes and folders only'

_M365_REGISTRY = Path.home() / ".gateway" / "m365_accounts.yaml"


def _normalize_addr(value: str) -> str:
    return value.strip().lower()


def _load_m365_upns() -> set[str]:
    if not _M365_REGISTRY.is_file():
        return set()
    try:
        data = yaml.safe_load(_M365_REGISTRY.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return set()
    if not isinstance(data, dict):
        return set()
    accounts = data.get("accounts")
    if not isinstance(accounts, dict):
        return set()
    return {_normalize_addr(str(key)) for key in accounts if str(key).strip()}


def _load_imap_upns() -> set[str]:
    raw = os.environ.get("EMAIL_BRIDGE_ACCOUNTS_JSON") or os.environ.get(
        "IMAP_ACCOUNTS_JSON"
    )
    if raw:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None
        entries: Any
        if isinstance(parsed, dict):
            entries = parsed.get("accounts", [])
        else:
            entries = parsed
        if isinstance(entries, list):
            users: set[str] = set()
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                user = str(
                    entry.get("user") or entry.get("username") or ""
                ).strip()
                if user:
                    users.add(_normalize_addr(user))
            if users:
                return users
    user = os.environ.get("IMAP_USER", "").strip()
    return {_normalize_addr(user)} if user else set()


def _looks_like_upn(value: str | None) -> bool:
    return bool(value and "@" in value)


def _mailbox_candidates(
    *,
    mailbox: str | None = None,
    account: str | None = None,
) -> list[str]:
    out: list[str] = []
    for raw in (mailbox, account):
        if _looks_like_upn(raw):
            out.append(_normalize_addr(str(raw)))
    return out


def wrong_surface_response(*, owning_surface: str, mailbox: str) -> dict[str, Any]:
    invoke = (
        _HEADLESS_EXPORT_INVOKE
        if owning_surface == _M365_GRAPH_ID
        else _IMAP_INVOKE
    )
    return {
        "error": "wrong_surface",
        "owning_surface": owning_surface,
        "invoke_hint": invoke,
        "message": (
            f"Mailbox {mailbox!r} is not served by the IMAP email-bridge; "
            f"use {invoke}."
        ),
    }


def unknown_mailbox_response(mailbox: str) -> dict[str, Any]:
    return {
        "error": "unknown_mailbox",
        "mailbox": mailbox,
        "known_surface_ids": list(_KNOWN_SURFACE_IDS),
        "message": (
            f"Mailbox {mailbox!r} is not indexed on imap_bridge and has no "
            "operator overlay binding; empty search is not proof of absence."
        ),
    }


def check_mailbox_surface(
    *,
    mailbox: str | None = None,
    account: str | None = None,
) -> dict[str, Any] | None:
    """Return a typed error dict when a UPN-like param targets the wrong surface."""
    m365 = _load_m365_upns()
    imap = _load_imap_upns()
    for candidate in _mailbox_candidates(mailbox=mailbox, account=account):
        on_m365 = candidate in m365
        on_imap = candidate in imap
        if on_m365 and not on_imap:
            return wrong_surface_response(
                owning_surface=_M365_GRAPH_ID, mailbox=candidate
            )
        if not on_imap and not on_m365 and m365:
            # Overlay present but mailbox unbound — still not an IMAP folder name.
            return unknown_mailbox_response(candidate)
    return None


def apply_indeterminate_if_degraded(
    result: dict[str, Any],
    *,
    status: dict[str, Any],
) -> dict[str, Any]:
    """When bridge is degraded and the caller got an empty set, surface uncertainty."""
    if result.get("error"):
        return result
    total = result.get("total")
    emails = result.get("emails")
    empty = (total == 0 if total is not None else False) or (
        isinstance(emails, list) and len(emails) == 0
    )
    if not empty:
        return result
    if status.get("healthy") is not False:
        return result
    reason = status.get("degraded_reason") or "email-bridge unhealthy"
    return {
        "status": "indeterminate",
        "reason": reason,
        "bridge_healthy": False,
        "message": (
            "email-bridge returned an empty result set while unhealthy; "
            "this is not proof that mail is absent."
        ),
        **result,
    }


def bridge_status_fetcher(relay: Any) -> dict[str, Any]:
    """Thin wrapper so mailbox ops can pass ``eb`` without importing relay here."""
    payload = relay("GET", "/status")
    return payload if isinstance(payload, dict) else {}
