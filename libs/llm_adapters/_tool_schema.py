"""Schema sanitation for client-side function tools.

Provider-native server tools (for example Anthropic's ``web_search`` /
``web_fetch`` built-ins or OpenAI/xAI ``type="mcp"`` entries) must pass through
unchanged. This helper only rewrites JSON Schema attached to client-side
``type="function"`` tools so providers receive a conservative, portable subset.
"""

from __future__ import annotations

from typing import Any

_DROP_KEYS: frozenset[str] = frozenset(
    {
        "additionalProperties",
        "default",
        "examples",
        "title",
    }
)


def _drop_null_branches(prop: dict[str, Any]) -> dict[str, Any]:
    """Remove ``null`` branches from ``anyOf`` and collapse when possible."""
    any_of = prop.get("anyOf")
    if not isinstance(any_of, list):
        return prop

    non_null = [branch for branch in any_of if branch != {"type": "null"}]
    if len(non_null) == len(any_of):
        return prop

    compact = {k: v for k, v in prop.items() if k != "anyOf"}
    if len(non_null) == 1 and isinstance(non_null[0], dict):
        compact.update(non_null[0])
    elif non_null:
        compact["anyOf"] = non_null
    return compact


def _prefer_object_branch(prop: dict[str, Any]) -> dict[str, Any]:
    """Promote an object branch when a union includes one.

    Client-side tool calls are emitted as JSON objects. Preferring the object
    branch keeps the wire schema acceptable to stricter providers like Gemini
    without changing the server-side parser contract.
    """

    any_of = prop.get("anyOf")
    if not isinstance(any_of, list):
        return prop

    object_branch = next(
        (
            branch
            for branch in any_of
            if isinstance(branch, dict) and branch.get("type") == "object"
        ),
        None,
    )
    if object_branch is None:
        return prop

    compact = {k: v for k, v in prop.items() if k != "anyOf"}
    compact.update(object_branch)
    return compact


def _sanitize_node(node: Any) -> Any:
    if isinstance(node, list):
        return [_sanitize_node(item) for item in node]
    if not isinstance(node, dict):
        return node

    cleaned: dict[str, Any] = {}
    for key, value in node.items():
        if key in _DROP_KEYS:
            continue
        cleaned[key] = _sanitize_node(value)

    cleaned = _drop_null_branches(cleaned)
    cleaned = _prefer_object_branch(cleaned)
    return cleaned


def sanitize_tool_parameters(schema: dict[str, Any]) -> dict[str, Any]:
    """Return a provider-safe function-parameters schema."""
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}}

    cleaned = _sanitize_node(schema)
    if cleaned.get("type") is None:
        cleaned = {"type": "object", **cleaned}
    if cleaned.get("type") == "object" and "properties" not in cleaned:
        cleaned["properties"] = {}
    return cleaned
