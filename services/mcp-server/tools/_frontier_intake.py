"""Intake normalization + validation for the ``team_dispatch`` MCP relay.

Pure, import-light helpers extracted from ``frontier.py`` so the three intake
behaviors (F16655 model ``-mcp`` strip, F16656 ``dispatch_thread_id`` presence,
F16657 ``messages[].content`` shape) are unit-testable without standing up
FastMCP, and so ``frontier.py`` stays off the SLOC ceiling.

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

_CONTENT_SHAPE_HINT = (
    "messages[].content must be a non-empty plain string. "
    'Example: {"role": "user", "content": "your message here"}'
)


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


def _validation_error(message: str, field: str = "") -> dict[str, Any]:
    env: dict[str, Any] = {"error": {"code": "validation_error", "message": message}}
    if field:
        env["field"] = field
    return env


def require_dispatch_thread_id(
    op: str, dispatch_thread_id: str
) -> dict[str, Any] | None:
    """Require ``dispatch_thread_id`` for generate/to_thread at intake (F16656).

    It is the server-owned compaction key for the ``team-dispatch`` pipeline.
    Previously an empty default forwarded a blank string that failed late and
    opaquely. The ticket framed this as generate-only, but the relay forwards
    the key on BOTH generate and to_thread, so the guard covers both.

    Returns an error envelope when missing/blank, else ``None``.
    """
    if op not in ("generate", "to_thread"):
        return None
    if not dispatch_thread_id or not dispatch_thread_id.strip():
        return _validation_error(
            "dispatch_thread_id is required for op='generate'/'to_thread' — "
            "minimal call: {op, role, model, dispatch_thread_id, messages}",
            field="dispatch_thread_id",
        )
    return None


def validate_dispatch_messages(
    messages: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Validate ``messages[].content`` shape at intake (F16657, policy B+).

    Accept only non-empty plain-string ``content``. Reject — early, with a
    field-named 422 and a block-detection hint — ``None``/missing/empty/
    whitespace-only content and any dict/list (LLM-API content-block) shape.
    No coercion: accepting block shapes would create a second input contract at
    a thin relay and risk silent data loss / provenance blurring (consult:
    gpt-5.5 reviewer, exec 55c64966). Also require >=1 ``role == "user"``
    message, since the pipeline expects the latest user turn.

    Returns ``None`` when clean, or an error envelope.
    """
    if not messages:
        return _validation_error(
            "messages must contain at least one user message. " + _CONTENT_SHAPE_HINT,
            field="messages",
        )

    saw_user = False
    for i, msg in enumerate(messages):
        if not isinstance(msg, dict):
            return _validation_error(
                f"messages[{i}] must be an object with 'role' and 'content'. "
                + _CONTENT_SHAPE_HINT,
                field=f"messages[{i}]",
            )
        if msg.get("role") == "user":
            saw_user = True
        content = msg.get("content")
        field = f"messages[{i}].content"
        if isinstance(content, str):
            if not content.strip():
                return _validation_error(
                    f"{field} is empty. " + _CONTENT_SHAPE_HINT, field=field
                )
            continue
        if isinstance(content, (dict, list)):
            return _validation_error(
                f"{field} must be a plain string, not {type(content).__name__}. "
                "You supplied LLM-API content blocks; extract the text before "
                "calling team_dispatch. " + _CONTENT_SHAPE_HINT,
                field=field,
            )
        return _validation_error(
            f"{field} must be a non-empty plain string (got "
            f"{type(content).__name__}). " + _CONTENT_SHAPE_HINT,
            field=field,
        )

    if not saw_user:
        return _validation_error(
            "messages must contain at least one message with role 'user'.",
            field="messages",
        )
    return None
