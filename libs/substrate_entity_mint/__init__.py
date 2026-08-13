"""Shared cortex entity_create author for the request-surface mint verb.

MCP ``substrate_entity_mint`` imports :func:`mint_entity` so cortex-api
``entity_create`` has one POST author. Domain isolation: mcp-server does not
import git_integration_worker, and this lib does not import either service.
"""

from __future__ import annotations

from substrate_entity_mint.mint import (
    ENTITY_CREATE_FORWARD,
    ENTITY_CREATE_OPTIONAL,
    ENTITY_CREATE_REQUIRED,
    mint_entity,
    resolve_create_slot,
)

__all__ = [
    "ENTITY_CREATE_FORWARD",
    "ENTITY_CREATE_OPTIONAL",
    "ENTITY_CREATE_REQUIRED",
    "mint_entity",
    "resolve_create_slot",
]
