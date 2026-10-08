"""Navigator wake dispatch — ``cursor-sdk`` (code).

A life-register wake is refused (``navigator_life_unsupported``); life seats
use ``life_dispatch``. CDP generate is not a navigator transport. A ``cdp/*``
``navigator_model`` is ignored on the wire so Stargate cannot route the wake
back onto project-ask.
"""

from __future__ import annotations

from typing import Any

from stargate_dispatch.client import submit_team_dispatch

from bus_watch.model_pause import model_paused

_LIFE_REGISTERS = frozenset({"cse", "life"})
_LIFE_SEATS = frozenset({"life"})
_CODE_SEATS = frozenset({"cursor-sdk", "cursor"})


def resolve_navigator_seat(policy: dict[str, Any] | None, *, register: str) -> str:
    """Return ``cursor-sdk``, or ``life`` when policy or register names the life seat.

    Submit of a life body is refused (``navigator_life_unsupported``). Life
    seats use ``life_dispatch``; this function does not route there.
    """
    raw = str((policy or {}).get("navigator_seat") or "").strip().lower()
    if raw in _CODE_SEATS:
        return "cursor-sdk"
    if raw in _LIFE_SEATS or register in _LIFE_REGISTERS:
        return "life"
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
    return None


def navigator_lane_id(body: dict[str, Any]) -> str:
    """Occupancy thread from a generate payload or a legacy request-shaped body."""
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
    if seat == "life":
        return {
            "register": register,
            "seat": "life",
            "dispatch_thread_id": tape,
            "parent_thread": tape,
            "work_key": work_key,
        }
    body = {
        "op": "generate",
        "seat": "cursor-sdk",
        "job": "freeform",
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
    """POST the wake. Default submit is Stargate ``team_dispatch`` for ``cursor-sdk``.

    A life-register body is refused. Life seats use ``life_dispatch``.
    """
    register = str(body.get("register") or "")
    if str(body.get("seat") or "") == "life" or register in _LIFE_REGISTERS:
        return {
            "error": {
                "code": "navigator_life_unsupported",
                "message": (
                    "life-register navigator wakes are unsupported (a:38728); "
                    "life seats use life_dispatch"
                ),
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
