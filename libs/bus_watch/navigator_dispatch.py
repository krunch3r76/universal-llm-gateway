"""Navigator wake dispatch — ``cursor-sdk`` (code) or ``cursor-auto`` (life).

CDP generate is not a navigator transport. A ``cdp/*`` ``navigator_model`` is
ignored on the wire so Stargate cannot route the wake back onto project-ask.
"""

from __future__ import annotations

from typing import Any

from stargate_dispatch.client import submit_team_dispatch

from bus_watch.model_pause import model_paused

_LIFE_REGISTERS = frozenset({"cse", "life"})
_LIFE_SEATS = frozenset({"cursor-auto", "life"})
_CODE_SEATS = frozenset({"cursor-sdk", "cursor"})


def resolve_navigator_seat(policy: dict[str, Any] | None, *, register: str) -> str:
    """Return ``cursor-sdk`` or ``cursor-auto``. Never ``cdp``."""
    raw = str((policy or {}).get("navigator_seat") or "").strip().lower()
    if raw in _CODE_SEATS:
        return "cursor-sdk"
    if raw in _LIFE_SEATS:
        return "cursor-auto"
    if register in _LIFE_REGISTERS:
        return "cursor-auto"
    return "cursor-sdk"


def model_for_navigator_seat(policy: dict[str, Any] | None, seat: str) -> str | None:
    """Cursor-family model only. ``cdp/*`` is dropped, not remapped to CDP.

    A paused navigator pin is not rewritten onto ``successor_model``. That
    substitution hid an Opus policy behind a grok wake.
    """
    raw = str((policy or {}).get("navigator_model") or "").strip()
    if model_paused(policy, raw):
        return None
    if seat == "cursor-sdk":
        if raw.startswith("cursor/"):
            return raw
        succ = str((policy or {}).get("successor_model") or "").strip()
        return succ if succ.startswith("cursor/") else None
    if seat == "cursor-auto" and raw.startswith("cursor/"):
        return raw
    return None


def navigator_lane_id(body: dict[str, Any]) -> str:
    """Occupancy thread from either generate or cursor_request shape."""
    for key in ("parent_thread", "dispatch_thread_id", "thread"):
        value = str(body.get(key) or "").strip()
        if value:
            return value
    return ""


def build_navigator_body(
    *,
    root_id: str,
    tape: str,
    doorbell: str,
    work_key: str,
    wake_timeout: float,
    policy: dict[str, Any],
    register: str,
    skills: list[str] | None = None,
) -> dict[str, Any]:
    """Assemble one navigator wake payload for ``submit_navigator``."""
    seat = resolve_navigator_seat(policy, register=register)
    model = model_for_navigator_seat(policy, seat)
    timeout = int(wake_timeout)
    if seat == "cursor-auto":
        body: dict[str, Any] = {
            "op": "request",
            "seat": "cursor-auto",
            "thread": tape,
            "subject": f"WAKE {root_id} navigator",
            "body": doorbell,
            "from_agent": "liaison-ticker",
            "to": "cursor",
            "contract": "recon",
            "work_key": work_key,
            "timeout_seconds": timeout,
            "caller_agent": "liaison-ticker",
        }
        if model:
            body["desired_model"] = model
        return body
    body = {
        "op": "generate",
        "seat": "cursor-sdk",
        "contract": "none",
        "lane": "A",
        "prompt": doorbell,
        "dispatch_thread_id": tape,
        "parent_thread": tape,
        "work_key": work_key,
        "timeout_seconds": timeout,
        "caller_agent": "liaison-ticker",
    }
    if model:
        body["model"] = model
    if skills:
        body["skills"] = skills
    return body


def submit_navigator(
    body: dict[str, Any],
) -> tuple[dict[str, Any], int]:
    """POST the wake. Life ``cursor-auto`` uses the injected ``submit`` in tests.

    Default production submit is Stargate ``team_dispatch`` for ``cursor-sdk``.
    ``cursor-auto`` must be submitted by the caller (life ``cursor_request``);
    a missing injector is a refused wake, not a silent CDP fallback.
    """
    if str(body.get("seat") or "") == "cursor-auto":
        return {
            "error": {
                "code": "cursor_auto_submit_required",
                "message": "life navigator wake uses cursor_request; inject submit",
            }
        }, 422
    return submit_team_dispatch(body)


__all__ = [
    "build_navigator_body",
    "model_for_navigator_seat",
    "navigator_lane_id",
    "resolve_navigator_seat",
    "submit_navigator",
]
