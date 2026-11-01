"""Validate and normalize house-claim tab addresses (work registry slice 1)."""

from __future__ import annotations

import json
import re
from typing import Any

_MAX_ADDRESS_BYTES = 2048
_SECRET_KEY_RE = re.compile(
    r"(?i)(password|secret|token|api_key|apikey|authorization|cookie|credential|private_key)"
)
_ASSERTION_WORK_KEY_RE = re.compile(r"^a:(\d+)$")


class AddressValidationError(ValueError):
    """422 ``ADDRESS_UNREACHABLE_SHAPE`` or secret-key refusal."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def display_work_key(work_key: str) -> str:
    """Map assertion ids ``a:NNNN`` to ``decision:a-NNNN`` for registry display."""
    match = _ASSERTION_WORK_KEY_RE.match(str(work_key or "").strip())
    if match is None:
        return work_key
    return f"decision:a-{match.group(1)}"


def _reject_secret_keys(address: dict[str, Any]) -> None:
    for key in address:
        if _SECRET_KEY_RE.search(str(key)):
            raise AddressValidationError(
                "ADDRESS_UNREACHABLE_SHAPE",
                f"address key {key!r} is not allowed",
            )


def normalize_address(address: dict[str, Any] | None) -> dict[str, Any] | None:
    """Validate shape and size; normalize display and grok defaults."""
    if address is None:
        return None
    if not isinstance(address, dict):
        raise AddressValidationError(
            "ADDRESS_UNREACHABLE_SHAPE", "address must be a JSON object"
        )
    _reject_secret_keys(address)
    raw = json.dumps(address, sort_keys=True, separators=(",", ":"))
    if len(raw.encode("utf-8")) > _MAX_ADDRESS_BYTES:
        raise AddressValidationError(
            "ADDRESS_UNREACHABLE_SHAPE", "address exceeds 2048 UTF-8 bytes"
        )

    kind = str(address.get("kind") or "").strip().lower()
    if kind == "cursor_sdk":
        dispatch_id = str(address.get("dispatch_id") or "").strip()
        if not dispatch_id:
            raise AddressValidationError(
                "ADDRESS_UNREACHABLE_SHAPE",
                "cursor_sdk address requires dispatch_id",
            )
        out = {"kind": "cursor_sdk", "dispatch_id": dispatch_id}
        worker_thread = str(address.get("worker_thread") or "").strip()
        if worker_thread:
            out["worker_thread"] = worker_thread
        worktree = str(address.get("worktree") or "").strip()
        if worktree:
            out["worktree"] = worktree
        return out
    if kind == "cursor_ide":
        gui_host = str(address.get("gui_host") or "").strip()
        if not gui_host:
            raise AddressValidationError(
                "ADDRESS_UNREACHABLE_SHAPE",
                "cursor_ide address requires gui_host",
            )
        out = {"kind": "cursor_ide", "gui_host": gui_host}
        tab = str(address.get("tab") or address.get("title") or "").strip()
        if tab:
            out["tab"] = tab
        agent_id = str(address.get("agent_id") or "").strip()
        if agent_id:
            out["agent_id"] = agent_id
        return out
    if kind == "cdp":
        thread_id = str(address.get("thread_id") or "").strip()
        if not thread_id:
            raise AddressValidationError(
                "ADDRESS_UNREACHABLE_SHAPE",
                "cdp address requires thread_id",
            )
        out = {"kind": "cdp", "thread_id": thread_id}
        chat_url = str(address.get("chat_url") or "").strip()
        if chat_url:
            out["chat_url"] = chat_url
        registration_id = str(address.get("registration_id") or "").strip()
        if registration_id:
            out["registration_id"] = registration_id
        return out
    if kind == "api_role":
        thread_id = str(address.get("thread_id") or "").strip()
        if not thread_id:
            raise AddressValidationError(
                "ADDRESS_UNREACHABLE_SHAPE",
                "api_role address requires thread_id",
            )
        return {"kind": "api_role", "thread_id": thread_id}
    if kind == "grok_bot":
        agent_id = str(address.get("agent_id") or "").strip()
        if not agent_id:
            raise AddressValidationError(
                "ADDRESS_UNREACHABLE_SHAPE",
                "grok_bot address requires agent_id",
            )
        out: dict[str, Any] = {
            "kind": "grok_bot",
            "agent_id": agent_id,
            "app": str(address.get("app") or "grok").strip().lower() or "grok",
        }
        display = str(address.get("display") or "").strip()
        if display:
            out["display"] = display.lstrip(":")
        tab_url = str(address.get("tab_url") or "").strip()
        if tab_url:
            out["tab_url"] = tab_url
        if "hidden" in address:
            hidden = address.get("hidden")
            out["hidden"] = hidden if isinstance(hidden, bool) else None
        return out

    cdp_url = str(address.get("cdp_url") or "").strip()
    target_id = str(address.get("target_id") or "").strip()
    tab_url_only = str(address.get("tab_url") or "").strip()
    if cdp_url and target_id:
        out = {"cdp_url": cdp_url, "target_id": target_id}
        if kind:
            out["kind"] = kind
        display = str(address.get("display") or "").strip()
        if display:
            out["display"] = display.lstrip(":")
        return out
    if tab_url_only:
        out = {"tab_url": tab_url_only}
        if kind:
            out["kind"] = kind
        display = str(address.get("display") or "").strip()
        if display:
            out["display"] = display.lstrip(":")
        return out

    raise AddressValidationError(
        "ADDRESS_UNREACHABLE_SHAPE",
        "address requires grok_bot+agent_id, cdp_url+target_id, or tab_url",
    )


def address_for_registry(stored: dict[str, Any] | None) -> dict[str, Any] | None:
    """Registry projection for grok_bot rows (paste_as_operator fields preserved)."""
    if not stored:
        return None
    kind = str(stored.get("kind") or "").strip().lower()
    if kind != "grok_bot":
        return dict(stored)
    out: dict[str, Any] = {
        "kind": "grok_bot",
        "agent_id": stored.get("agent_id"),
        "app": stored.get("app") or "grok",
    }
    if stored.get("display") is not None:
        out["display"] = str(stored.get("display")).lstrip(":")
    if stored.get("tab_url") is not None:
        out["tab_url"] = stored.get("tab_url")
    if "hidden" in stored:
        out["hidden"] = stored.get("hidden")
    return out
