"""Author-field reconciliation for agent_bus — ``from=`` alias + surface autofill."""

from __future__ import annotations

from typing import Any

from request_profile import current_request_metadata

# Canonical bus addresses when the caller omits from=/from_agent on a dual mount.
_SURFACE_DEFAULT_FROM: dict[str, str] = {
    "life": "web-anthropic",
    "code": "cursor",
}

AUTHOR_AUTOFILL_OPS: frozenset[str] = frozenset(
    {"send", "request", "hop", "post", "reply", "triage", "update_thread"}
)


def resolve_dispatch_from_agent(from_agent: str = "") -> tuple[str, dict[str, Any] | None]:
    """Resolve author for a dispatcher after MCP-layer reconciliation."""
    args, err = reconcile_author_arguments({"from_agent": from_agent})
    if err is not None:
        return "", err
    return str(args["from_agent"]), None


def default_from_for_surface(surface: str | None) -> str | None:
    """Return the canonical bus author for an MCP mount surface, if known."""
    if not surface:
        return None
    return _SURFACE_DEFAULT_FROM.get(surface)


def reconcile_author_arguments(
    parsed: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Normalize author aliases and autofill from the active MCP surface.

    ``from=`` is the preferred wire name; ``from_agent`` is the permanent alias.
    Explicit ``from_agent`` / ``from`` always wins over surface autofill.
    """
    args = dict(parsed)
    if "from" in args:
        args.setdefault("from_agent", args.pop("from"))
    if "agent" in args:
        args.setdefault("from_agent", args.pop("agent"))

    from_agent = args.get("from_agent")
    if isinstance(from_agent, str) and from_agent.strip():
        args["from_agent"] = from_agent.strip()
        return args, None

    surface = current_request_metadata().get("surface")
    default = default_from_for_surface(
        surface if isinstance(surface, str) else None
    )
    if default:
        args["from_agent"] = default
        return args, None

    return args, {
        "error": (
            "from_agent (or alias from=) is required when the MCP mount surface "
            "cannot be inferred — pass an explicit seat address "
            '(e.g. "cursor", "web-anthropic") or call from /mcp/life or /mcp/code.'
        ),
        "reason": "from_agent_required",
        "missing_fields": ["from_agent"],
    }
