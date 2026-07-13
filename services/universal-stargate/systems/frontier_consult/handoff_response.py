"""Handoff result-handle construction for POST /api/v1/team/handoff.

Builds the typed result_handle + handoff_status + poll_hint fragment appended
to the handoff response. Kept separate from route.py (thin) and handoff.py
(thread creation) per SRP.

The handle's ``kind`` is authoritative for source-of-truth routing.

- ``kind == "agent_bus_thread"`` (handoff): poll the bus only; never the
  pipeline tracker. No pseudo ``execution_id`` is minted.
- ``kind == "dual"`` (generate with default bus delivery): bus-first precedence —
  ``thread_id`` is authoritative (poll via ``poll_hint`` / agent-bus wait);
  ``execution_id`` is pollable for the full lifecycle when ``durable: true``
  (dispatch-link persisted at admit); otherwise the bus thread remains the
  durable surface.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Literal

from .mcp_capability import mcp_capability_fields

if TYPE_CHECKING:
    from agent_seat.profiles import CapabilityProfile

# Ship C: only observable pre-reply state (no read_at-derived push proxy).
HandoffStatus = Literal["awaiting_first_reply"]

_INITIAL_HANDOFF_STATUS: HandoffStatus = "awaiting_first_reply"


def _apply_mcp_capability_fields(
    caps: dict[str, Any],
    *,
    substrate: str,
    model: str,
    mcp_enabled: bool | None = None,
) -> dict[str, Any]:
    effective_mcp = (
        mcp_enabled
        if mcp_enabled is not None
        else not bool(caps.get("inline_only", False))
    )
    caps.update(
        mcp_capability_fields(
            substrate=substrate,
            model=model,
            mcp_enabled=bool(effective_mcp),
        )
    )
    caps.pop("mcp_connector_active", None)
    return caps


def build_result_handle(*, thread_id: str, after_turn: int = 1) -> dict[str, Any]:
    """Typed handle identifying the agent-bus thread as source of truth.

    ``after_turn`` is the pointer turn the handoff just created; a reply
    is any turn with number > after_turn from the web seat.
    """
    return {
        "kind": "agent_bus_thread",
        "thread_id": thread_id,
        "after_turn": after_turn,
    }


# friction 23653 / 24081: the attended Cursor IDE MCP client can orphan a long
# blocking agent_bus(wait) tools/call — the server completes and emits
# wait.completed within the 75s relay budget, but the IDE spinner stays open
# (client-transport orphan) until the operator interrupts (~30 min). A 0s
# snapshot returns in milliseconds, so the IDE never holds the call open long
# enough to orphan; re-polling is instant. Web/API seats do not share this
# transport failure mode and keep the 60s server-side block (fewer round-trips).
_CURSOR_IDE_POLL_WAIT_SECONDS = 0
_DEFAULT_POLL_WAIT_SECONDS = 60


def resolve_poll_wait_seconds(
    *, caller_agent: str | None = None, poller_is_cursor_ide: bool = False
) -> int:
    """Wait window to recommend to the polling seat via ``poll_hint``.

    Cursor-attended pollers get a 0s snapshot; every other seat keeps the 60s
    server-side block. ``poller_is_cursor_ide`` forces the snapshot for surfaces
    whose poller is definitionally the Cursor IDE lead (e.g. cursor-sdk generate)
    regardless of the agent-supplied ``caller_agent``.
    """
    if poller_is_cursor_ide or (caller_agent or "").strip().lower() == "cursor":
        return _CURSOR_IDE_POLL_WAIT_SECONDS
    return _DEFAULT_POLL_WAIT_SECONDS


def build_poll_hint_wait(
    *,
    thread_id: str,
    from_agent: str,
    after_turn: int = 1,
    wait_seconds: int = _DEFAULT_POLL_WAIT_SECONDS,
) -> dict[str, Any]:
    """Canonical poll_hint (Phase 2+): server-side wait args.

    fetch is now only a fallback; the wait op is the documented retrieval path.
    ``from_agent`` is the web seat whose first reply completes the handoff.
    ``wait_seconds`` is the recommended server-side block; resolve it with
    ``resolve_poll_wait_seconds`` so Cursor-attended pollers get a 0s snapshot.

    ``arguments`` is a dict for human inspection; ``arguments_json`` is the
    MCP wire form (``agent_bus.arguments`` must be a JSON string).
    """
    wait_args = {
        "thread": thread_id,
        "after_turn": after_turn,
        "wait_seconds": wait_seconds,
        "completion": "first_reply_from",
        "from_agent": from_agent,
    }
    return {
        "tool": "wait",
        "arguments": wait_args,
        "arguments_json": json.dumps(wait_args, separators=(",", ":")),
    }


def build_recommended_executor(
    *,
    handoff_contract: str,
    packet_text: str,
    executor_override: str | None = None,
    executor_override_reason_code: str | None = None,
    executor_override_reason: str | None = None,
) -> dict[str, Any]:
    """Assemble executor advisory fields for the handoff response."""
    from .executor_resolution import derive_recommended_executor

    return derive_recommended_executor(
        handoff_contract,
        packet_text,
        executor_override=executor_override,
        executor_override_reason_code=executor_override_reason_code,
        executor_override_reason=executor_override_reason,
    )


def build_executor_recommendation_field(
    *,
    handoff_contract: str,
    target_surface: str,
    target_model: str,
) -> dict[str, Any]:
    """Additive ``executor_recommendation`` field for the handoff response.

    Wraps the shared surface-agnostic builder. Does NOT read or mutate the legacy
    ``recommended_executor`` advisory fields.
    """
    from dispatch_knob_policy import build_executor_recommendation

    return {
        "executor_recommendation": build_executor_recommendation(
            contract=handoff_contract,
            target_surface=target_surface,
            target_model=target_model,
            classification_source="packet_contract",
        )
    }


def build_recommended_review(*, handoff_contract: str) -> dict[str, Any]:
    """Consult adversarial-pass advisory (Q-sibling dual)."""
    from .executor_resolution import derive_recommended_review

    value = derive_recommended_review(handoff_contract)
    return {"recommended_review": value}


def build_seat_capability(
    *,
    profile: CapabilityProfile,
    recommended_executor: str | None,
) -> dict[str, Any]:
    """Seat-capability advisory block for the handoff response."""
    return {
        "seat_capability": {
            "delivery": profile.delivery,
            "api_dispatchable": profile.api_dispatchable,
            "auto_dispatchable": profile.auto_dispatchable,
            "manual_handoff": profile.manual_handoff,
            "tool_surface": profile.tool_surface,
            "picker_range": list(profile.allowed_models),
            "default_model": profile.default_model,
            "recommended_executor": recommended_executor,
        }
    }


def build_push_reminder(
    *,
    thread_id: str,
    to_agent: str,
    platform: str,
    handoff_contract: str | None = None,
) -> str:
    """Operator-facing reminder.

    Web seats need a bus push; cursor seats need IDE attendance.
    """
    seat_obligation = (
        " Dispatching seat: translate this for the operator in your reply — "
        "what was dispatched and to whom, what runs autonomously, exactly what "
        "the operator must do, and how results return. Do not paste this "
        "reminder verbatim."
    )
    if platform == "sdk":
        return (
            f"**Automated cursor-sdk dispatch**: handoff posted to thread "
            f"{thread_id}. Worker admission runs asynchronously; poll via "
            f"poll_hint for the first reply from cursor-sdk."
            f"{seat_obligation}"
        )
    if platform == "cursor":
        executor_hint = (
            " Default executor for bound implement handoffs is Composer "
            "(Thinking ≡ Fast); pick Composer in the model picker unless the "
            "server advisory names a structured override."
            if handoff_contract == "implement"
            else ""
        )
        return (
            f"**Action needed — attend agent-bus in Cursor**: handoff posted to thread "
            f"{thread_id}. Open the thread in Cursor (Multitask or /agent-bus) as "
            f"{to_agent}.{executor_hint}"
            f"{seat_obligation}"
        )
    return (
        f"**Action needed — push to web claude**: handoff posted to thread "
        f"{thread_id}. Push the agent-bus message to trigger {to_agent}'s turn."
        f"{seat_obligation}"
    )


def build_handoff_result(
    *,
    thread_id: str,
    to_agent: str,
    reply_from_agent: str | None = None,
    after_turn: int = 1,
    poll_wait_seconds: int = _DEFAULT_POLL_WAIT_SECONDS,
) -> dict[str, Any]:
    """Assemble the three additive handoff-response fields.

    ``to_agent`` is the bus recipient address for the dispatch turn.
    ``reply_from_agent`` is the predicted closeout author for ``poll_hint``;
    defaults to ``to_agent`` when omitted (manual handoff / API-role generate).
    ``poll_wait_seconds`` is the recommended server-side wait window for the
    polling seat (``resolve_poll_wait_seconds``): 0 for the Cursor-attended IDE
    lead, 60 for web/API seats.

    The returned dict includes a top-level ``reply_from_agent`` scalar
    (``reply_from_agent or to_agent``) that exactly matches
    ``poll_hint.arguments.from_agent`` — the canonical value to pass to
    ``agent_bus(tool="wait", from_agent=...)``. Resolves friction 20435.
    """
    return {
        "result_handle": build_result_handle(
            thread_id=thread_id, after_turn=after_turn
        ),
        "handoff_status": _INITIAL_HANDOFF_STATUS,
        "reply_from_agent": reply_from_agent or to_agent,
        "poll_hint": build_poll_hint_wait(
            thread_id=thread_id,
            from_agent=reply_from_agent or to_agent,
            after_turn=after_turn,
            wait_seconds=poll_wait_seconds,
        ),
    }


def build_api_generate_result(
    *,
    role: str,
    profile: CapabilityProfile,
    handoff_fields: dict[str, Any],
    dispatch_result: dict[str, Any],
    thread_id: str,
    resolved_model: str,
    resolved_contract: str,
    durable: bool = False,
    density_triage: str | None = None,
    review_opt_out_reason_code: str | None = None,
    auto_review_child: bool = False,
) -> dict[str, Any]:
    """Generate-shaped 202 for API roles with default bus thread delivery."""
    from .densify_triage import build_generate_review_envelope

    execution_id = dispatch_result.get("execution_id")
    result: dict[str, Any] = {
        **dispatch_result,
        **handoff_fields,
        **build_generate_review_envelope(
            density_triage=density_triage,
            review_opt_out_reason_code=review_opt_out_reason_code,
            auto_review_child=auto_review_child,
        ),
        "op": "generate",
        "output_contract": "thread",
        "thread_id": thread_id,
        "thread": thread_id,
        "to_agent": role,
        "resolved_model": resolved_model or dispatch_result.get("resolved_model"),
        "resolved_contract": resolved_contract,
        "substrate": "api",
        "poll_hint": handoff_fields["poll_hint"],
        "result_handle": {
            # dual: bus-first — thread_id via poll_hint; execution_id inline fallback
            "kind": "dual",
            "execution_id": execution_id,
            "thread_id": thread_id,
            "substrate": "api",
            "durable": durable,
        },
    }
    if "capabilities" not in result and profile is not None:
        result["capabilities"] = {
            "role": role,
            "resolved_model": resolved_model,
            "tool_surface": profile.tool_surface,
            "substrate": "api",
        }
    caps = result.get("capabilities")
    if isinstance(caps, dict):
        _apply_mcp_capability_fields(
            caps,
            substrate="api",
            model=resolved_model or str(caps.get("resolved_model") or ""),
        )
    return result


def build_sdk_generate_result(
    *,
    role: str,
    profile: CapabilityProfile,
    handoff_fields: dict[str, Any],
    execution_id: str,
    thread_id: str,
    to_agent: str,
    resolved_model: str,
    resolved_contract: str,
    warnings: list[Any],
    durable: bool = False,
    density_triage: str | None = None,
    review_opt_out_reason_code: str | None = None,
    auto_review_child: bool = False,
) -> dict[str, Any]:
    """Generate-shaped 202 response for SDK-substrate cursor-sdk dispatch."""
    from .densify_triage import build_generate_review_envelope

    result: dict[str, Any] = {
        **handoff_fields,
        **build_generate_review_envelope(
            density_triage=density_triage,
            review_opt_out_reason_code=review_opt_out_reason_code,
            auto_review_child=auto_review_child,
        ),
        "op": "generate",
        "execution_id": execution_id,
        "substrate": "sdk",
        "output_contract": "thread",
        "thread_id": thread_id,
        "thread": thread_id,
        "to_agent": to_agent,
        "resolved_model": resolved_model,
        "resolved_contract": resolved_contract,
        "capabilities": {
            "role": role,
            "inline_only": False,
            "tool_surface": profile.tool_surface,
            "resolved_model": resolved_model,
            "substrate": "sdk",
            **_apply_mcp_capability_fields(
                {},
                substrate="sdk",
                model=resolved_model,
            ),
        },
        "poll_hint": handoff_fields["poll_hint"],
        "result_handle": {
            # dual: bus-first — thread_id via poll_hint; execution_id inline fallback
            "kind": "dual",
            "execution_id": execution_id,
            "thread_id": thread_id,
            "substrate": "sdk",
            "durable": durable,
        },
    }
    if warnings:
        result["warnings"] = warnings
    return result
