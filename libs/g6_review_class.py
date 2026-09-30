"""G6 review-class bound for filtered cursor-sdk seats.

Nested ``implement`` / ``pure-mechanical`` seats may call ``team_dispatch``
only in the pre-land review shape. The CDP worker uses
``review_fallback_model`` to retry ``cdp/fable`` once when Opus completes
without proof. Unfiltered seats (top-level ``handoff_contract=conductor``)
are not gated here — they already see the full code primary set.
"""

from __future__ import annotations

from typing import Any

REVIEW_PURPOSE = "review"
FALLBACK_MODEL = "cdp/fable"
PRIMARY_REVIEW_MODELS = frozenset({"cdp/opus-5", "cdp/opus-5.5"})
# ``cdp/fable`` is an allowed entry model when the seat already knows Opus
# returned without proof. Automatic retry does not chain: fable is not primary.
REVIEW_MODELS = PRIMARY_REVIEW_MODELS | {FALLBACK_MODEL}
STALL_COMPLETED_WITHOUT_PROOF = "completed_without_proof"
# After the inflight clock is reset, keep the fable leg inside the 3600s horizon.
FALLBACK_WALL_CAP_S = 1200.0

# Fail closed. A key that is not listed is refused.
ALLOWED_ARGUMENT_KEYS = frozenset(
    {
        "op",
        "purpose",
        "model",
        "contract",
        "prompt",
        "dispatch_thread_id",
        "reasoning_effort",
        "parent_thread",
    }
)

_REFUSAL_MESSAGE = (
    "team_dispatch on a filtered cursor-sdk seat is review-class only "
    "(op=generate, job=delivery-review, model in "
    "{cdp/opus-5, cdp/opus-5.5, cdp/fable}, job=freeform). "
    "seat, role, nest_under, lane, packet_path, and source_ref are refused."
)


def _present(value: Any) -> bool:
    if value is None or value is False:
        return False
    if value == "" or value == [] or value == {}:
        return False
    return True


def fallback_wall_s(primary_wall_s: float) -> float:
    """Wall budget for the fable leg after ``upsert_inflight_leg`` resets the clock."""
    return min(float(primary_wall_s), FALLBACK_WALL_CAP_S)


def is_review_class_call(
    arguments: dict[str, Any] | None,
    *,
    seat_thread: str | None = None,
) -> bool:
    """True when *arguments* are a G6 review generate and nothing else.

    Unknown keys are refused. When *seat_thread* is set, ``dispatch_thread_id``
    must be that thread and ``parent_thread`` may only repeat it.
    """
    args = arguments or {}
    for key, value in args.items():
        if not _present(value):
            continue
        if key not in ALLOWED_ARGUMENT_KEYS:
            return False
    if args.get("op") != "generate":
        return False
    if args.get("purpose") != REVIEW_PURPOSE:
        return False
    if args.get("model") not in REVIEW_MODELS:
        return False
    if args.get("contract") != "none":
        return False
    if seat_thread:
        if str(args.get("dispatch_thread_id") or "") != seat_thread:
            return False
        parent = args.get("parent_thread")
        if _present(parent) and str(parent) != seat_thread:
            return False
    return True


def adopt_review_fallback(*, ok: bool, body: str) -> bool:
    """Keep the fable result only when it actually carries a verdict body."""
    return bool(ok) and bool(body.strip())


def review_fallback_model(
    *,
    purpose: str | None,
    model_id: str | None,
    stall_stage: str | None,
) -> str | None:
    """Model to run once after Opus review completes without proof.

    ``cdp/fable`` is not itself a primary: a second miss does not chain.
    """
    if (purpose or "").strip() != REVIEW_PURPOSE:
        return None
    if stall_stage != STALL_COMPLETED_WITHOUT_PROOF:
        return None
    if (model_id or "").strip() not in PRIMARY_REVIEW_MODELS:
        return None
    return FALLBACK_MODEL


def refuse_filtered_team_dispatch(
    message: dict[str, Any],
    *,
    seat_thread: str | None = None,
) -> dict[str, Any] | None:
    """JSON-RPC error for a non-review ``team_dispatch`` tools/call, else None.

    Non-``team_dispatch`` calls are not this function's concern. A review-class
    call returns None so the bridge forwards it.
    """
    if message.get("method") != "tools/call":
        return None
    params = message.get("params")
    if not isinstance(params, dict):
        return None
    if params.get("name") != "team_dispatch":
        return None
    arguments = params.get("arguments")
    if isinstance(arguments, str):
        return _refusal(message, _REFUSAL_MESSAGE)
    if arguments is not None and not isinstance(arguments, dict):
        return _refusal(message, _REFUSAL_MESSAGE)
    if is_review_class_call(
        arguments if isinstance(arguments, dict) else {},
        seat_thread=seat_thread,
    ):
        return None
    return _refusal(message, _REFUSAL_MESSAGE)


def refuse_filtered_tools_call(
    message: dict[str, Any],
    *,
    allow: frozenset[str],
    seat_thread: str | None = None,
) -> dict[str, Any] | None:
    """Refuse a tools/call outside *allow*, then apply the review gate.

    ``tools/list`` trimming does not stop a call by name. The allow set is the
    contract primary list; ``team_dispatch`` stays on that list and is further
    limited to the review shape.
    """
    if message.get("method") != "tools/call":
        return None
    params = message.get("params")
    if not isinstance(params, dict):
        return _refusal(message, "tools/call params must be an object")
    name = params.get("name")
    if not isinstance(name, str) or name not in allow:
        return _refusal(
            message,
            f"tool {name!r} is not on this contract's primary list",
        )
    if name == "team_dispatch":
        return refuse_filtered_team_dispatch(message, seat_thread=seat_thread)
    return None


def _refusal(message: dict[str, Any], text: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": message["id"] if "id" in message else None,
        "error": {"code": -32602, "message": text},
    }
