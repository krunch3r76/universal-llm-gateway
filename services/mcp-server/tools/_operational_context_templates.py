"""Static protocol templates for ``render_operational_context``.

Re-exports all constants from the two theme sub-modules so existing
importers (``_operational_context.py``) remain unchanged.

Pure string constants — no env-var interpolation, no agent substitution.
Extracted from ``_operational_context.py`` to keep that module under
SLOC budget per [quality]. Imports are unconditional and one-way; this
module does not import from ``_operational_context``.

Templates that depend on env-vars (CORTEX_OWNER_NAME, etc.) live in
``_operational_context.py`` where the env-var resolution happens.
Templates that contain ``{agent}`` substitution markers are also here —
the renderer calls ``.format(agent=...)`` at render time; the markers
are static, only the substitution is dynamic.
"""

from __future__ import annotations

from ._oc_knowledge_templates import (
    AGENT_BUS_COMPACT,
    AGENT_BUS_EXAMPLES,
    AGENT_BUS_LARGE_PAYLOADS,
    ASSERTION_SEARCH,
    CORTEX_RETRIEVAL_WORKFLOWS,
    CORTEX_SCHEMA_PREAMBLE,
    JOURNALING_PROTOCOL,
    SANDBOX_MAP,
    SESSION_CLOSE_MARKDOWN_AUDIT,
    THREAD_LIFECYCLE,
    TRANSCRIPT_CLOSE_PROTOCOL,
)
from ._oc_surface_templates import (
    ADDENDA_BLOCKS,
    BEHAVIORAL_RULES,
    CLAUDE_WEB_TOOL_SURFACE,
    CURSOR_LOCAL_ENFORCEMENT,
    FRONTIER_MODEL_ROUTING,
    FRONTIER_REASONING,
    render_frontier_reasoning,
    GROK_DIRECT_SESSION_CLOSE,
    GROK_WEB_TOOL_SURFACE,
    MCP_TOOL_SEARCH,
    NOTES_TO_SELF,
    ON_DEMAND_POINTERS,
    PROSE_DISCIPLINE_SCOPE,
    SUBAGENT_INHERITANCE,
    TEAM_CONSULTATION,
    TOOL_REFERENCE_POINTERS,
    WEB_SESSION_CLOSE_GENERIC,
    WEB_TRANSCRIPT_PREPROCESSING,
)

__all__ = [
    "ADDENDA_BLOCKS",
    "AGENT_BUS_COMPACT",
    "AGENT_BUS_EXAMPLES",
    "AGENT_BUS_LARGE_PAYLOADS",
    "ASSERTION_SEARCH",
    "BEHAVIORAL_RULES",
    "CLAUDE_WEB_TOOL_SURFACE",
    "CORTEX_RETRIEVAL_WORKFLOWS",
    "CORTEX_SCHEMA_PREAMBLE",
    "CURSOR_LOCAL_ENFORCEMENT",
    "FRONTIER_MODEL_ROUTING",
    "FRONTIER_REASONING",
    "render_frontier_reasoning",
    "GROK_DIRECT_SESSION_CLOSE",
    "GROK_WEB_TOOL_SURFACE",
    "JOURNALING_PROTOCOL",
    "MCP_TOOL_SEARCH",
    "NOTES_TO_SELF",
    "ON_DEMAND_POINTERS",
    "PROSE_DISCIPLINE_SCOPE",
    "SANDBOX_MAP",
    "SESSION_CLOSE_MARKDOWN_AUDIT",
    "SUBAGENT_INHERITANCE",
    "TEAM_CONSULTATION",
    "THREAD_LIFECYCLE",
    "TOOL_REFERENCE_POINTERS",
    "TRANSCRIPT_CLOSE_PROTOCOL",
    "WEB_SESSION_CLOSE_GENERIC",
    "WEB_TRANSCRIPT_PREPROCESSING",
]
