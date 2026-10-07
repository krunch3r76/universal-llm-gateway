"""Heuristic scope expansion for unscoped rag-search (default scope ``all``).

When the query reads as human writing / ceremonial prose (not agent session-close
protocol), replace the broad ``all`` composite with writing- and research-oriented
scopes so agent-skills ceremony docs do not dominate lexical overlap.
"""

from __future__ import annotations

# Scopes named in assertion:38634 / Fonzi arc 15420 observation.
_WRITING_RESEARCH_SCOPES: tuple[str, ...] = (
    "writing",
    "writing_exemplars",
    "llm_writing_anti",
    "research",
)

_AGENT_SESSION_CLOSE_CUES: tuple[str, ...] = (
    "session close",
    "session-close",
    "session_close",
    "session-close-kernel",
    "close(op=",
    "cortex.session_close",
    "orchestrator continuity",
    "agent_bus",
    "checkpoint",
    "handoff protocol",
    "session close preflight",
)

_WRITING_PROSE_CUES: tuple[str, ...] = (
    "ceremonial",
    "gratitude",
    "thank you for your attention",
    "thank-you for your attention",
    "closing remark",
    "closing statement",
    "formal close",
    "literary close",
    "valedict",
    "sign-off",
    "sign off",
    "outbound prose",
    "ghostwrit",
    "writing register",
    "writing craft",
    "prose register",
    "audience attention",
    "llm close",
    "model close",
    "thank you for listening",
    "words of thanks",
)


def classify_unscoped_writing_intent(query: str) -> bool:
    """True when the query targets human writing corpus, not agent close protocol."""
    normalized = " ".join(query.lower().split())
    if not normalized:
        return False
    if any(cue in normalized for cue in _AGENT_SESSION_CLOSE_CUES):
        return False
    return any(cue in normalized for cue in _WRITING_PROSE_CUES)


def expand_default_scopes_for_query(
    query: str,
    default_scopes: list[str],
) -> tuple[list[str], bool]:
    """Return (scopes, expanded).

    Only expands when defaults are exactly ``[\"all\"]`` and writing intent matches.
    """
    if default_scopes != ["all"]:
        return default_scopes, False
    if not classify_unscoped_writing_intent(query):
        return default_scopes, False
    return list(_WRITING_RESEARCH_SCOPES), True
