"""Tool surface for the multi-model agent loop.

Sync tool-call dispatcher used by Stargate's native tool loop. Tool schema
definitions are sourced from ``libs/agent_seat/tools.py`` (single source of
truth shared with the pipeline ``frontier_dispatch_v1`` handler). Cortex ops
relay to cortex-api ``POST /dispatch``; agent_bus uses ``.agent_bus.AGENT_BUS_OPS``.
"""

from __future__ import annotations

import json
from typing import Annotated, Any

from agent_seat import (
    TEAM_TOOL_DEFINITIONS as TEAM_TOOL_DEFINITIONS,  # noqa: PLC0414 (re-export)
)
from agent_seat import (
    TOOL_DEFINITIONS as TOOL_DEFINITIONS,  # noqa: PLC0414 (re-export)
)
from pydantic.functional_validators import BeforeValidator

SYSTEM_PROMPT = """\
You are an advisory agent with access to a structured knowledge system (Cortex).

## Cortex
Entities: people, accounts, legal matters, organizations, decisions, documents. \
Each has assertions — claims with confidence levels (confirmed, believed, \
suspected, hypothesized), evidence, and optional temporal scope (valid_from, \
valid_until for time-bounded facts like balances and due dates).

Entity IDs use type:slug format: person:jane-doe, decision:api-migration-v2, \
service:rag, todo:section-aware-chunking.

## Approach
1. Use tools to gather evidence before answering — check relevant entities, \
assertions, and relationships.
2. Give direct, actionable advice. Do not hedge unnecessarily.
3. Cite specific entities and assertions when referencing data.
4. If information conflicts, call it out explicitly.
5. State your confidence level and reasoning.\
"""


def parse_dispatch_arguments(raw: object) -> dict[str, Any] | None:
    """Parse dispatch-style arguments (JSON string or dict). None on failure.

    The MCP tool schemas declare ``arguments: string`` — that's the canonical
    wire form for every supported MCP client. Dict passthrough is retained as
    defense-in-depth for non-MCP callers that invoke the same handlers
    directly with already-parsed payloads (e.g. internal test helpers).
    """
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def _coerce_dispatch_arguments(v: object) -> object:
    """Coerce a dict to a JSON string before Pydantic validates the ``str`` type.

    Agents occasionally produce ``arguments={...}`` (object literal) instead of
    the canonical ``arguments='{"...": "..."}'`` wire form.  This
    ``BeforeValidator`` normalises the input transparently so the FastMCP handler
    receives a valid JSON string in either case.

    The outer Pydantic type annotation stays ``str``, so the generated JSON
    Schema remains ``{"type": "string"}`` — satisfying the ``mcp-tool-param-types``
    invariant that forbids ``anyOf / object`` on optional params.
    """
    if isinstance(v, dict):
        return json.dumps(v)
    return v


# Type alias: ``str`` wire form with automatic dict→JSON coercion at the MCP
# boundary.  Use this instead of bare ``str`` for dispatch-style ``arguments``
# params so agents that pass an object literal are handled gracefully.
# JSON Schema stays ``{"type": "string"}`` — see _coerce_dispatch_arguments.
JsonArgStr = Annotated[str, BeforeValidator(_coerce_dispatch_arguments)]


# Dispatch-style MCP tools: the inner ``arguments`` field is a JSON-encoded
# object string. The schema deliberately declares ``arguments: str`` — Claude.ai's
# MCP client silently drops optional params with anyOf/object JSON Schema
# (``mcp-tool-param-types`` invariant), so widening this to an object/union shape
# is NOT an option. Single source of truth for the tool set so descriptors, docs,
# and tests cannot drift. See decision:dispatch-arguments-string-wire-form.
DISPATCH_STYLE_TOOLS: frozenset[str] = frozenset(
    {"cortex", "agent_bus", "agent_bus_read", "rag", "dispatch"}
)

# A failed parse of a *string* ``arguments`` is almost always an escaping problem
# on a large, quote-heavy payload (frictions 12886, 17227 — session_close with
# embedded quotes / JSON snippets / code fences). Point the caller at the safe
# offload paths instead of leaving them to re-escape by hand.
_DISPATCH_ARGS_OFFLOAD_HINT = (
    " If the payload contains quotes, newlines, or embedded JSON/code fences "
    "(e.g. a large transcript_md, session_summary_md, or handoff_prompt), do not "
    "hand-build the JSON string: write the payload to a file and pass a "
    "file-path parameter instead (session_close: session_summary_md_path / "
    "transcript_jsonl_path / handoff_source_path / source_ref), "
    "or use the /agent-bus CLI, which bypasses MCP shape validation."
)


def classify_dispatch_args_raw(raw: object) -> str:
    """Classify a failed dispatch-args payload for instrumentation.

    Two classes, pinned by spec §9 (mcp-dispatch-args-parity-sf1-sf2-sf7):
      ``whole_object_literal`` — caller passed an object/dict. ``JsonArgStr``
        coercion handles this at the MCP boundary, so this class should trend
        to ~zero once every dispatch tool uses ``JsonArgStr``.
      ``malformed_string`` — caller hand-built a JSON string that did not parse
        (the mis-escape failure mode ``JsonArgStr`` cannot fix). This is the
        gate signal for the deferred client-serialization helper
        (todo:dispatch-args-client-serialization-helper): build it only if
        ``malformed_string`` dominates the failure distribution over time.
    """
    return "whole_object_literal" if isinstance(raw, dict) else "malformed_string"


def _record_dispatch_args_invalid(*, raw_kind: str, tool: str | None) -> None:
    """Emit a structured dispatch-args parse-failure event (best-effort).

    The shared error builder is the single choke point for every dispatch-style
    parse failure, so it is the natural place to instrument the deferred
    client-serialization decision. The import is lazy + guarded so non-MCP
    direct callers (and any context without the event bus) never break on the
    instrumentation. See spec §9 / decision:dispatch-arguments-string-wire-form.
    """
    try:
        from mcp_events import record

        record("mcp.dispatch.arguments.invalid", raw_kind=raw_kind, tool=tool or "")
    except Exception:  # noqa: BLE001 — instrumentation must never break the error path
        pass


def dispatch_arguments_error(
    raw: object, *, example: str, tool: str | None = None
) -> dict[str, str]:
    """Build the standard dispatch-style "arguments did not parse" error.

    Single source of truth for the message emitted when
    ``parse_dispatch_arguments`` returns ``None`` across the dispatch-style MCP
    surfaces (cortex/agent_bus/agent_bus_read/rag/dispatch). When ``raw`` is a
    ``str`` (the canonical wire form) the message appends an offload hint, since a
    failed string parse is almost always an escaping failure on a large
    quote-heavy payload. Emits a structured ``raw_kind`` event so the deferred
    client-serialization decision is measurable. See
    decision:dispatch-arguments-string-wire-form.
    """
    _record_dispatch_args_invalid(raw_kind=classify_dispatch_args_raw(raw), tool=tool)
    message = (
        f"arguments must be a JSON-encoded object string (e.g. '{example}'); "
        f"got {type(raw).__name__} that did not parse as a JSON object"
    )
    if isinstance(raw, str):
        message += _DISPATCH_ARGS_OFFLOAD_HINT
    return {"error": message}
