"""Post-process MCP tool schemas for wire size — strip FastMCP bloat.

FastMCP generates verbose JSON Schema from Python type hints:
- ``anyOf: [{type: X}, {type: null}]`` for every ``X | None`` param
- ``default: null`` on every optional
- ``outputSchema`` (rarely consumed by clients)
- ``_meta: {fastmcp: {tags: []}}`` metadata
- ``default: false`` / ``default: ""`` / ``default: 0`` for obvious defaults

A server-level ``Transform`` wraps each tool at ``tools/list`` time so
``to_mcp_tool()`` emits compact schemas on the wire.  ~30-40% reduction on
typical tool sets.  Underlying ``Tool.parameters`` are untouched so overflow
metadata capture (pre-prune) stays consistent.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from fastmcp.server.transforms import GetToolNext, Transform
from fastmcp.utilities.versions import VersionSpec

if TYPE_CHECKING:
    from fastmcp import FastMCP
    from fastmcp.tools.base import Tool

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


def _drop_null_branches(prop: dict[str, Any]) -> dict[str, Any]:
    """Remove ``{type: null}`` branches from ``anyOf``; collapse if only one remains.

    FastMCP emits ``anyOf: [X, {type: null}]`` (and 3-branch variants like
    ``[string, object, null]``) for optional params. Strict wire-schema
    validators (xAI, Gemini — see google/adk-python #3424) reject ``anyOf``
    when the property lacks a sibling top-level ``type``. LLMs treat optional
    params as "omit if unused" rather than sending ``null`` explicitly, so
    dropping null branches on the wire is safe: the Python type hint still
    admits ``None`` and Pydantic validates inputs against the in-memory schema
    independently of the wire schema.
    """
    any_of = prop.get("anyOf")
    if not isinstance(any_of, list):
        return prop

    non_null = [b for b in any_of if isinstance(b, dict) and b != {"type": "null"}]
    if len(non_null) == len(any_of):
        return prop

    compact = {k: v for k, v in prop.items() if k != "anyOf"}
    if len(non_null) == 1:
        compact.update(non_null[0])
    elif len(non_null) > 1:
        compact["anyOf"] = non_null
    return compact


def _prefer_object_branch(prop: dict[str, Any]) -> dict[str, Any]:
    """For ``anyOf: [string, object, ...]``: promote object branch to top level.

    When a union contains an object branch, strict wire-schema validators
    (xAI, OpenAI strict mode) need a single top-level ``type``. Structured
    clients (LLMs that emit tool arguments) prefer objects; legacy string-form
    callers are absorbed by server-side JSON parsing. Picking the object branch
    satisfies the validator and matches the canonical LLM call shape.
    """
    any_of = prop.get("anyOf")
    if not isinstance(any_of, list) or len(any_of) < 2:
        return prop

    object_branch: dict[str, Any] | None = None
    for branch in any_of:
        if isinstance(branch, dict) and branch.get("type") == "object":
            object_branch = branch
            break

    if object_branch is None:
        return prop

    compact = {k: v for k, v in prop.items() if k != "anyOf"}
    compact.update(object_branch)
    if compact.get("default") == "{}":
        compact["default"] = {}
    return compact


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

        prop = _drop_null_branches(prop)
        prop = _prefer_object_branch(prop)

        if "default" in prop and _is_trivial_default(prop["default"]):
            prop = {k: v for k, v in prop.items() if k != "default"}

        compact_props[name] = prop

    result = dict(schema)
    result["properties"] = compact_props
    return result


def _compact_mcp_tool(mcp_tool: Any) -> Any:
    """Apply the three wire-schema mutations in order (matches legacy patch)."""
    if isinstance(mcp_tool.inputSchema, dict):
        mcp_tool.inputSchema = minify_schema(mcp_tool.inputSchema)
    mcp_tool.outputSchema = None
    mcp_tool.meta = None
    return mcp_tool


class _CompactToolProxy:
    """Delegate to a Tool but emit compact schemas from ``to_mcp_tool``."""

    __slots__ = ("_tool",)

    def __init__(self, tool: Tool) -> None:
        self._tool = tool

    def to_mcp_tool(self, **overrides: Any) -> Any:
        return _compact_mcp_tool(self._tool.to_mcp_tool(**overrides))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._tool, name)


class CompactSchemaTransform(Transform):
    """FastMCP list-tools transform — compact schemas at serialization time."""

    async def list_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        return [_CompactToolProxy(t) for t in tools]

    async def get_tool(
        self, name: str, call_next: GetToolNext, *, version: VersionSpec | None = None
    ) -> Tool | None:
        tool = await call_next(name, version=version)
        return _CompactToolProxy(tool) if tool else None


def register_compact_schema_transform(mcp: FastMCP) -> None:
    """Register compact-schema transform on the FastMCP server instance.

    Must run after all tools are registered and before any ``tools/list``
    response is served.
    """
    mcp.add_transform(CompactSchemaTransform())
