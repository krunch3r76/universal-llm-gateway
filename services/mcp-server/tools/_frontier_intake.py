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

# F16655. The ``-mcp`` suffix is a load-bearing feature of the *llm_generate*
# tool (``tools/llm.py``): appending it makes the cloud proxy inject Cortex/RAG
# tool definitions for a caller-driven manual tool loop. ``team_dispatch`` runs
# the tool loop automatically, so the suffix is meaningless here and otherwise
# leaks into the model id sent downstream (silently routed to the compat shunt).
# Strip it at THIS intake only — never in llm.py, where the suffix is intended.
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
    auto_review_child: bool = False,
) -> dict[str, Any] | None:
    """Admission guard for ``contract=wrap`` on the MCP relay."""
    if contract != "wrap":
        return None
    if op != "generate":
        return _validation_error(
            "contract=wrap is only valid with op='generate', role='cursor-sdk'",
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
