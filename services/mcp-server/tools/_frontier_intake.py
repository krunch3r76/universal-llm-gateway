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
            "minimal call: {op, role, model, dispatch_thread_id, contract}",
            field="dispatch_thread_id",
        )
    return None
