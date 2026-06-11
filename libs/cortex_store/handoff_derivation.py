"""Handoff derivation labels and write limits (stdlib-only).

Shared by read-side projection (``handoff_surface``) and write-side resolution
(``handoff_resolution``) without pulling FastAPI into MCP import paths.
"""

from __future__ import annotations

DERIVATION_SECTION = "section"
DERIVATION_SECTION_UNRESOLVED = "section_unresolved"
DERIVATION_SECTION_AMBIGUOUS = "section_ambiguous"
DERIVATION_DETACHED_STRING = "detached_string"
DERIVATION_AUTO_PERSISTED = "auto_persisted"

WRITE_PATH_SESSION_CLOSE = "session_close"

HANDOFF_PROMPT_MAX_CHARS = 128_000
HANDOFF_PROVENANCE_JSON_MAX_BYTES = 65_536
