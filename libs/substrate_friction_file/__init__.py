"""Shared cortex friction author for the request-surface file verb.

MCP ``substrate_friction_file`` imports :func:`file_friction` so cortex-api
``friction`` has one POST author. Domain isolation: mcp-server does not import
git_integration_worker, and this lib does not import either service.
"""

from __future__ import annotations

from substrate_friction_file.file import file_friction, resolve_friction_note

# Harvest nominates these manage slugs when this lib lands (package-grain).
CONSUMERS: tuple[str, ...] = ('mcp',)

__all__ = ["file_friction", "resolve_friction_note"]
