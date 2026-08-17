"""MCP argument unwrapping, identity/target extraction, and bounded detail.

Holds the wire-dict helpers that every toolCall parser shares: nested
``arguments`` JSON decode, effective-arg merge, fs-compact detail, and the
cortex/bus/fs/rag target+identity extractors. Invariant: detail payloads
larger than ``surface_taxonomy.DetailCap`` collapse to a truncated stub
(``ResultCap``); never raise on unparsed dicts. This module depends only on
``surface_taxonomy`` (tool frozensets + caps) and must not import
``effect_entries`` or ``cortex_surface`` (those call into here).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from . import surface_taxonomy

def _fs_compact_detail(effective: Mapping[str, Any]) -> dict[str, Any] | None:
    detail: dict[str, Any] = {}
    op = _string_arg(effective, "op")
    sandbox = _string_arg(effective, "sandbox")
    path = _string_arg(effective, "path")
    if op:
        detail["op"] = op
    if sandbox:
        detail["sandbox"] = sandbox
    if path:
        detail["path"] = path
    return detail or None


def _bounded_detail(value: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not value:
        return None
    try:
        text = json.dumps(dict(value), separators=(",", ":"))
    except (TypeError, ValueError):
        return {"raw": str(value)[:surface_taxonomy.DetailCap]}
    if len(text) <= surface_taxonomy.DetailCap:
        return dict(value)
    return {"truncated": text[:surface_taxonomy.ResultCap]}


def _nested_tool_arguments(args: Mapping[str, Any]) -> Mapping[str, Any]:
    raw = args.get("arguments")
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, Mapping) else {}
        except (json.JSONDecodeError, TypeError, ValueError):
            return {}
    if isinstance(raw, Mapping):
        return raw
    return {}


def _effective_mcp_args(nested: Mapping[str, Any]) -> Mapping[str, Any]:
    inner = _nested_tool_arguments(nested)
    if not inner:
        return nested
    merged: dict[str, Any] = dict(nested)
    merged.update(inner)
    return merged


def _mcp_target(tool_name: str, args: Mapping[str, Any]) -> str | None:
    if tool_name in surface_taxonomy._CORTEX_TOOLS:
        return _string_arg(args, "entity_id", "assertion_id", "id", "tool")
    if tool_name in surface_taxonomy._AGENT_BUS_TOOLS:
        thread = _string_arg(args, "new_slug", "slug", "thread_id", "thread")
        turn = _string_arg(args, "turn_number", "turn")
        if thread and turn:
            return f"{thread}#{turn}"
        return thread
    if tool_name in surface_taxonomy._FS_TOOLS:
        sandbox = _string_arg(args, "sandbox")
        path = _string_arg(args, "path")
        if sandbox and path:
            return f"{sandbox}:{path}"
        return path
    if tool_name in surface_taxonomy._RAG_TOOLS:
        return _string_arg(args, "op", "scope", "source_hash", "path")
    return _string_arg(args, "operation", "tool", "service")


def _mcp_identity(tool_name: str, args: Mapping[str, Any]) -> str | None:
    if tool_name in surface_taxonomy._CORTEX_TOOLS:
        return _string_arg(args, "entity_id", "assertion_id", "id")
    if tool_name in surface_taxonomy._FS_TOOLS:
        sandbox = _string_arg(args, "sandbox")
        path = _string_arg(args, "path")
        if sandbox and path:
            return f"{sandbox}:{path}"
        return path
    if tool_name in surface_taxonomy._RAG_TOOLS:
        return _string_arg(args, "source_hash", "op", "path")
    return _mcp_target(tool_name, args)


def _string_arg(args: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, int) and not isinstance(value, bool):
            return str(value)
    return None
