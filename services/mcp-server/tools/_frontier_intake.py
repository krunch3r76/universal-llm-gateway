"""Intake normalization + validation for the ``team_dispatch`` MCP relay.

Pure, import-light helpers extracted from ``frontier.py`` so the three intake
behaviors (F16655 model ``-mcp`` strip, F16656 ``dispatch_thread_id`` presence)
are unit-testable without standing up FastMCP, and so ``frontier.py`` stays off
the SLOC ceiling.

Error-envelope shape matches ``frontier.py``'s existing returns::

    {"error": {"code": "validation_error", "message": "..."}, "field"?: "..."}

A validation helper returns ``None`` when intake is clean, or an error-envelope
dict the tool returns verbatim.
"""

from __future__ import annotations

from typing import Any

from universal_logging import get_logger

logger = get_logger(__name__)

# F16655. The ``-mcp`` suffix is a cloud-proxy model-id variant for agentic
# clients on ``/v1/chat/completions`` (see cloud catalog synthetic rows).
# ``team_dispatch`` runs the tool loop automatically, so the suffix is
# meaningless here and otherwise leaks into the model id sent downstream
# (silently routed to the compat shunt). Strip it at THIS intake only.
_MCP_SUFFIX = "-mcp"
_CURSOR_SDK_ROLE = "cursor-sdk"


def normalize_dispatch_model(model: str | None) -> str | None:
    """Strip a single trailing ``-mcp`` suffix from a dispatch model id (F16655).

    ``openai/gpt-5.5-mcp`` -> ``openai/gpt-5.5``. No-op for ``None`` or ids
    without the suffix. Real catalog ids never end in ``-mcp`` (e.g.
    ``-search-api``, ``-reasoning``, ``-hybrid`` are untouched), so an exact
    suffix match is safe.
    """
    if model is None:
        return None
    if model.endswith(_MCP_SUFFIX):
        stripped = model[: -len(_MCP_SUFFIX)]
        logger.warning(
            "team_dispatch: stripped %r suffix from model_id=%r -> %r",
            _MCP_SUFFIX,
            model,
            stripped,
        )
        return stripped
    return model


def _validation_error(
    message: str,
    field: str = "",
    *,
    code: str = "validation_error",
) -> dict[str, Any]:
    env: dict[str, Any] = {"error": {"code": code, "message": message}}
    if field:
        env["field"] = field
    return env


def require_dispatch_thread_id(
    op: str,
    dispatch_thread_id: str,
    contract: str | None = None,
) -> dict[str, Any] | None:
    """Require ``dispatch_thread_id`` for generate/to_thread at intake (F16656).

    ``contract=wrap`` on ``op=generate`` is exempt — wrap never reads the
    dispatch-thread body and never spawns an SDK thread.

    Returns an error envelope when missing/blank, else ``None``.
    """
    if op not in ("generate", "to_thread"):
        return None
    if op == "generate" and contract == "wrap":
        return None
    if not dispatch_thread_id or not dispatch_thread_id.strip():
        return _validation_error(
            "dispatch_thread_id is required for op='generate'/'to_thread' — "
            "minimal call: {op, role, model, dispatch_thread_id, contract}",
            field="dispatch_thread_id",
        )
    return None


def validate_wrap_inputs(
    op: str,
    contract: str | None,
    role_is_sdk: bool,
    packet_path: str | None,
    source_ref: str | None,
    *,
    density_triage: str | None = None,
    review_opt_out_reason_code: str | None = None,
    auto_review_child: bool | None = None,
) -> dict[str, Any] | None:
    """Admission guard for ``contract=wrap`` on the MCP relay."""
    if contract != "wrap":
        return None
    if op != "generate":
        return _validation_error(
            "contract=wrap is only valid with op='generate', seat='cursor-sdk'",
            field="contract",
        )
    if not role_is_sdk:
        return _validation_error(
            "contract=wrap is only admitted on the cursor-sdk generate branch",
            field="role",
            code="wrap_role_not_admitted",
        )
    if source_ref is None:
        return _validation_error(
            "source_ref is required for contract=wrap",
            field="source_ref",
            code="wrap_requires_source_ref",
        )
    if packet_path is not None:
        return _validation_error(
            "packet_path is forbidden for contract=wrap; use source_ref only",
            field="packet_path",
            code="wrap_with_packet_path",
        )
    for field_name, value in (
        ("density_triage", density_triage),
        ("review_opt_out_reason_code", review_opt_out_reason_code),
    ):
        if value is not None:
            return _validation_error(
                f"{field_name} is not applicable for contract=wrap",
                field=field_name,
                code="wrap_field_not_applicable",
            )
    if auto_review_child:
        return _validation_error(
            "auto_review_child is not applicable for contract=wrap",
            field="auto_review_child",
            code="wrap_field_not_applicable",
        )
    return None


def reject_unsupported_packet_inputs(
    op: str,
    contract: str | None,
    packet_path: str | None,
    source_ref: str | None,
) -> dict[str, Any] | None:
    """Reject packet_path/source_ref where downstream cannot honor them (F17378)."""
    if op not in ("generate", "to_thread"):
        return None
    if packet_path is None and source_ref is None:
        return None
    if op == "to_thread":
        field = "packet_path" if packet_path is not None else "source_ref"
        return _validation_error(
            "packet_path/source_ref are not honored for op='to_thread' "
            "(API-role delivery has no cursor-sdk worker packet channel).",
            field=field,
        )
    if source_ref is not None and contract not in ("implement", "wrap"):
        return _validation_error(
            "source_ref materialization is only supported for contract='implement' "
            "(cursor-sdk implement lane); remove source_ref or set contract='implement'.",
            field="source_ref",
        )
    return None


def validate_inline_prompt_inputs(
    op: str,
    contract: str | None,
    packet_path: str | None,
    source_ref: str | None,
    prompt: str | None,
    sidecar_ref: str | None,
) -> dict[str, Any] | None:
    """Reject explicit prompt inputs that would be ignored or ambiguous."""
    inline_fields = [
        field
        for field, value in (("prompt", prompt), ("sidecar_ref", sidecar_ref))
        if value is not None
    ]
    if op == "handoff" and inline_fields:
        field = inline_fields[0]
        return _validation_error(
            f"{field} is only supported on op='generate'/'to_thread'; "
            "handoff uses packet_path or source_ref",
            field=field,
            code="inline_prompt_not_supported",
        )
    if contract in ("implement", "wrap") and inline_fields:
        field = inline_fields[0]
        return _validation_error(
            f"{field} is not supported with contract={contract!r}; "
            "use packet_path or source_ref for implement/wrap",
            field=field,
            code="inline_prompt_not_supported",
        )
    if source_ref is not None and inline_fields:
        return _validation_error(
            "source_ref cannot be combined with prompt or sidecar_ref",
            field=inline_fields[0],
            code="multiple_prompt_sources",
        )
    explicit_fields = [
        field
        for field, value in (
            ("packet_path", packet_path),
            ("prompt", prompt),
            ("sidecar_ref", sidecar_ref),
        )
        if value is not None
    ]
    if len(explicit_fields) > 1:
        return _validation_error(
            "explicit prompt sources are mutually exclusive; pass exactly one "
            f"of packet_path, prompt, or sidecar_ref (received {explicit_fields})",
            field=explicit_fields[1],
            code="multiple_prompt_sources",
        )
    return None


def reject_supersede_wire_field(supersede: Any) -> dict[str, Any] | None:
    """Reject ``supersede`` on team_dispatch — not shipped; use ``force`` instead."""
    if supersede is None:
        return None
    return _validation_error(
        "supersede is not supported on team_dispatch in this release; "
        "use force=true to bypass same-source_ref reject on implement admits",
        field="supersede",
        code="supersede_not_supported",
    )


def validate_force_on_implement(
    force: bool,
    contract: str | None,
) -> dict[str, Any] | None:
    """``force`` bypasses same-``source_ref`` reject only; valid on implement."""
    if not force:
        return None
    if contract != "implement":
        return _validation_error(
            "force is only valid with contract='implement'",
            field="force",
            code="force_implement_only",
        )
    return None


def reject_pointer_body_on_generate(
    op: str,
    pointer_body: str | None,
) -> dict[str, Any] | None:
    """Reject pointer_body outside op='handoff' (friction 23301).

    Previously accepted and silently dropped on generate/to_thread — the
    4741/4742 blind-panel briefs never reached the model. Those ops use an
    explicit prompt source or a role-gated latest-turn fallback; neither has a
    pointer_body channel.
    """
    if op == "handoff" or pointer_body is None:
        return None
    return _validation_error(
        "pointer_body is handoff-only. On op='generate'/'to_thread', pass the "
        "brief atomically via prompt or sidecar_ref; packet_path is also "
        "supported on generate. The role-gated dispatch-thread latest turn is "
        "fallback only. Use panel_dispatch messages[] for panel briefs.",
        field="pointer_body",
    )


# Manual-handoff alias table for claude-cursor (mirrors admission.py resolve_handoff_seat).
# Do NOT import Stargate modules into MCP intake.
_CLAUDE_CURSOR_SEAT_ALIASES: frozenset[str] = frozenset(
    {"claude-cursor", "cursor", "cursor-claude"}
)

# Role shortcuts that route to claude-cursor when no explicit seat= is present.
_CURSOR_ROLES: frozenset[str] = frozenset({"cursor-consult", "cursor-implement"})


def require_explicit_cursor_seat_for_handoff(
    op: str,
    seat: str | None,
    role: str | None,
) -> dict[str, Any] | None:
    """Reject op=handoff that routes to claude-cursor without explicit seat=.

    Returns None (clean) or an error-envelope dict (caller returns verbatim).
    Policy: decision:handoff-default-seat-claude-web (#19925).
    """
    if op != "handoff":
        return None

    seat_norm = (seat or "").strip().lower()
    if seat_norm in _CLAUDE_CURSOR_SEAT_ALIASES:
        return (
            None  # explicit seat present — guard satisfied (F3 compat bridge allowed)
        )

    role_norm = (role or "").strip().lower()
    if role_norm not in _CURSOR_ROLES:
        return None  # role does not target claude-cursor — not our concern

    if seat_norm and seat_norm not in _CURSOR_ROLES:
        return None  # explicit non-cursor seat wins over cursor role shorthand (AC5)

    return {
        "error": {
            "code": "handoff_claude_cursor_requires_explicit_seat",
            "message": (
                "op=handoff to claude-cursor requires explicit seat selection via "
                "seat='claude-cursor' (or alias 'cursor'). "
                "role='cursor-consult' and role='cursor-implement' route to the Cursor IDE "
                "seat, which requires the operator to explicitly pick it "
                "(decision:handoff-default-seat-claude-web). "
                "Default handoffs use role='web-consult' (claude-web). "
                "For Cursor IDE implement: pass seat='claude-cursor', contract='implement'."
            ),
        },
        "field": "seat",
        "details": {
            "blocked_role": role_norm,
            "required_seat": "claude-cursor",
            "policy": "decision:handoff-default-seat-claude-web",
        },
    }
