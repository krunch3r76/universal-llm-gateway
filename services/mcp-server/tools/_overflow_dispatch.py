"""Overflow dispatch helpers for op+arguments domain tools (email, etc.).

When ``dispatch(tool="email", arguments='{"op": "get", "message_id": "..."}')``
spreads parsed JSON as ``email(op="get", message_id=...)``, Python raises
``TypeError: unexpected keyword argument`` before the tool body runs.
Preflight in ``server.dispatch`` returns a structured hint before calling the
overflow tool; TypeError enrichment covers any remaining boundary cases.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
from collections.abc import Callable
from typing import Any

_UNEXPECTED_KWARG_RE = re.compile(r"unexpected keyword argument '([^']+)'")


async def call_overflow_tool(fn: Callable[..., Any], kwargs: dict[str, Any]) -> Any:
    """Invoke a registry tool function from ``server.rag`` / ``server.dispatch``
    without blocking the event loop.

    ``overflow_registry`` holds raw ``FunctionTool.fn`` callables, most of them
    synchronous and I/O-bound (``rag_search`` blocks on Stargate for up to
    minutes). Calling them inline froze the single uvicorn loop — every tool on
    both surfaces, and every new TLS handshake, waited behind one search; four
    "parallel" searches ran strictly one after another. Coroutine functions are
    awaited directly; sync functions run on the default thread pool with the
    caller's ``contextvars`` (request profile) copied in.
    """
    if inspect.iscoroutinefunction(fn):
        return await fn(**kwargs)
    result = await asyncio.to_thread(fn, **kwargs)
    if asyncio.iscoroutine(result):
        return await result
    return result


def tool_schema_has_nested_arguments(schema: dict[str, Any] | None) -> bool:
    """True when *schema* describes an ``op`` + ``arguments`` domain tool, i.e. one
    whose per-op parameters travel inside a nested JSON string."""
    props = (schema or {}).get("properties", {}) or {}
    return "op" in props and "arguments" in props


def flat_op_args_in_dispatch_payload(parsed: dict[str, Any]) -> dict[str, Any]:
    """Return op-specific keys mistakenly placed at dispatch top level."""
    if "arguments" in parsed:
        return {}
    op = parsed.get("op")
    if not isinstance(op, str) or not op:
        return {}
    return {
        k: v for k, v in parsed.items() if k not in ("op", "tool") and v is not None
    }


def nested_arguments_dispatch_error(
    tool: str,
    *,
    op: str,
    flat_keys: list[str],
) -> dict[str, Any]:
    """Build the ``DispatchShapeError`` envelope that names the misplaced top-level
    keys and shows the corrected nested-``arguments`` call for *tool*/*op*."""
    keys = ", ".join(flat_keys[:5])
    inner_example = (
        '{"message_id": "<msg-id>"}'
        if "message_id" in flat_keys
        else '{"mailbox": "Sent", "limit": 20}'
    )
    inner_escaped = inner_example.replace('"', '\\"')
    return {
        "error": (
            f"{tool}() got unexpected keyword argument(s): {keys}. "
            "Op-specific parameters belong inside the nested "
            '"arguments" JSON string, not as top-level dispatch keys.'
        ),
        "error_type": "DispatchShapeError",
        "tool": tool,
        "op": op,
        "accepted_params": ["op", "arguments"],
        "hint": (
            f'Use dispatch(tool="{tool}", arguments=\'{{"op": "{op}", '
            f'"arguments": "{inner_escaped}"}}\'). '
            "See agent_skill:email-tool-dispatch."
        ),
        "example": (
            f'dispatch(tool="{tool}", arguments=\'{{"op": "{op}", '
            f'"arguments": "{inner_escaped}"}}\')'
        ),
    }


def preflight_nested_op_dispatch(
    tool: str,
    parsed: dict[str, Any],
    schema: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Return a shape-error envelope before the tool runs when op parameters were
    placed at dispatch top level; ``None`` when the payload is well-formed."""
    if not tool_schema_has_nested_arguments(schema):
        return None
    flat = flat_op_args_in_dispatch_payload(parsed)
    if not flat:
        return None
    op = str(parsed.get("op") or "")
    return nested_arguments_dispatch_error(tool, op=op, flat_keys=sorted(flat))


def enrich_type_error_for_nested_op(
    tool: str,
    parsed: dict[str, Any] | None,
    exc: BaseException,
    schema: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Translate an ``unexpected keyword argument`` TypeError raised by a nested-op
    tool into the shape-error envelope; ``None`` for any other exception."""
    if not isinstance(exc, TypeError):
        return None
    if not tool_schema_has_nested_arguments(schema):
        return None
    m = _UNEXPECTED_KWARG_RE.search(str(exc))
    if not m:
        return None
    op = ""
    flat_keys = [m.group(1)]
    if isinstance(parsed, dict):
        op = str(parsed.get("op") or "")
        flat_keys = sorted(flat_op_args_in_dispatch_payload(parsed) or flat_keys)
    return nested_arguments_dispatch_error(tool, op=op or "<op>", flat_keys=flat_keys)


def coerce_nested_op_dispatch_call(
    parsed: dict[str, Any],
    schema: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Build kwargs for ``fn(op=..., arguments=...)`` from a flat dispatch payload."""
    if not tool_schema_has_nested_arguments(schema):
        return None
    flat = flat_op_args_in_dispatch_payload(parsed)
    if not flat:
        return None
    op = str(parsed.get("op") or "list")
    return {"op": op, "arguments": json.dumps(flat, separators=(",", ":"))}
