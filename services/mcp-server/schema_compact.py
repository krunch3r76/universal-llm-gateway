"""Post-process MCP tool schemas for wire size — strip FastMCP bloat.

FastMCP generates verbose JSON Schema from Python type hints:
- ``anyOf: [{type: X}, {type: null}]`` for every ``X | None`` param
- ``default: null`` on every optional
- ``outputSchema`` (rarely consumed by clients)
- ``_meta: {fastmcp: {tags: []}}`` metadata
- ``default: false`` / ``default: ""`` / ``default: 0`` for obvious defaults

This module patches ``FunctionTool.to_mcp_tool`` to strip that bloat before
the schema reaches the wire.  ~30-40% reduction on typical tool sets.
"""

from __future__ import annotations

from typing import Any

_TRIVIAL_DEFAULTS: set[type] = {type(None), bool, int, float, str}

_TRIVIAL_DEFAULT_VALUES: dict[type, set[object]] = {
    type(None): {None},
    bool: {False},
    int: {0},
    float: {0.0},
    str: {""},
}


def _is_trivial_default(value: object) -> bool:
    """True if default is the zero-value for its type (null, false, 0, 0.0, "")."""
    vtype = type(value)
    if vtype not in _TRIVIAL_DEFAULTS:
        return False
    return value in _TRIVIAL_DEFAULT_VALUES.get(vtype, set())


def _simplify_nullable(prop: dict[str, Any]) -> dict[str, Any]:
    """Collapse ``anyOf: [{type: X}, {type: null}]`` → ``type: [X, null]``.

    Only applies to the simple case where the non-null branch is a bare
    ``{type: X}`` with no extra keys (items, properties, etc.).
    """
    any_of = prop.get("anyOf")
    if not isinstance(any_of, list) or len(any_of) != 2:
        return prop

    null_branch = None
    other_branch = None
    for branch in any_of:
        if not isinstance(branch, dict):
            return prop
        if branch == {"type": "null"}:
            null_branch = branch
        else:
            other_branch = branch

    if null_branch is None or other_branch is None:
        return prop

    if list(other_branch.keys()) == ["type"]:
        compact = dict(prop)
        del compact["anyOf"]
        compact["type"] = [other_branch["type"], "null"]
        return compact

    return prop


def minify_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Compact a JSON Schema dict: strip trivial defaults, simplify nullables."""
    if not isinstance(schema, dict):
        return schema

    props = schema.get("properties")
    if not isinstance(props, dict):
        return schema

    compact_props: dict[str, Any] = {}
    for name, prop in props.items():
        if not isinstance(prop, dict):
            compact_props[name] = prop
            continue

        prop = _simplify_nullable(prop)

        if "default" in prop and _is_trivial_default(prop["default"]):
            prop = {k: v for k, v in prop.items() if k != "default"}

        compact_props[name] = prop

    result = dict(schema)
    result["properties"] = compact_props
    return result


def patch_fastmcp_tool_serialization() -> None:
    """Monkey-patch FunctionTool.to_mcp_tool to emit compact schemas.

    Must be called once at import time, before any tools/list requests.
    """
    from fastmcp.tools.function_tool import FunctionTool

    _orig = FunctionTool.to_mcp_tool

    def _compact_to_mcp(self: FunctionTool, **overrides: Any) -> Any:
        mcp_tool = _orig(self, **overrides)
        if isinstance(mcp_tool.inputSchema, dict):
            mcp_tool.inputSchema = minify_schema(mcp_tool.inputSchema)
        mcp_tool.outputSchema = None
        mcp_tool.meta = None
        return mcp_tool

    FunctionTool.to_mcp_tool = _compact_to_mcp  # type: ignore[assignment]
