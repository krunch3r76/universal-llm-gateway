"""Module-level constants for skill ingest tooling."""

from __future__ import annotations

import re

_CANONICAL_DOC_RE = re.compile(
    r"universal-llm-gateway/docs/agent-guides/skills/([A-Za-z0-9_-]+)\.md"
)
_CORTEX_SOT_RE = re.compile(
    r"SOT:[\s`*]*"
    r"(?:cortex://agent-skills/"
    r'|fs\(sandbox="cortex",\s*op="read",\s*path="agent-skills/)'
    r"([A-Za-z0-9_-]+)\.md"
)
_SUPPRESSED = frozenset({"deprecated", "retired"})
_CREATE_SUPPRESSED_LIFECYCLES = frozenset({"deprecated", "retired", "merged"})
_WS = "workspaces://universal-llm-gateway"
_SYNC_SOURCE_URI = f"{_WS}/docs/agent-guides/skills/skill-document-writing.md"
_SKIP_CORTEX_SOT = frozenset({"README"})
