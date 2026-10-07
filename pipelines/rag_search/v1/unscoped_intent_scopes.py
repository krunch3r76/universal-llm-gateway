"""Heuristic scope expansion for unscoped rag-search (default scope ``all``).

High-precision writing cues replace ``all`` with writing/research scopes so
agent-skills ceremony docs do not dominate. Weak cues append those scopes
alongside ``all`` so legal, agent, and general corpus stay reachable.
"""

from __future__ import annotations

from typing import Literal

# Scopes named in assertion:38634 / Fonzi arc 15420 observation.
_WRITING_RESEARCH_SCOPES: tuple[str, ...] = (
    "writing",
    "writing_exemplars",
    "llm_writing_anti",
    "research",
)

_AGENT_SESSION_CLOSE_CUES: tuple[str, ...] = (
    "session close",
    "session closing",
    "session-close",
    "session_close",
    "session-close-kernel",
    "close(op=",
    "cortex.session_close",
    "orchestrator",
    "orchestrator continuity",
    "agent_bus",
    "agent bus",
    "checkpoint",
    "handoff protocol",
    "session close preflight",
    "closeout",
    " lane ",
    " lane",
    "dispatch",
    "cursor-sdk",
)

# Replace ``all`` entirely — fixes ceremony-in-all dominance (assertion:38634).
_HIGH_PRECISION_WRITING_CUES: tuple[str, ...] = (
    "ceremonial",
    "gratitude",
    "thank you for your attention",
    "thank-you for your attention",
    "valedict",
    "ghostwrit",
)

# Append writing scopes; keep ``all`` for mixed or ambiguous prose queries.
_WEAK_WRITING_CUES: tuple[str, ...] = (
    "closing remark",
    "formal close",
    "literary close",
    "outbound prose",
    "writing register",
    "writing craft",
    "prose register",
    "audience attention",
    "llm close",
    "model close",
    "thank you for listening",
    "words of thanks",
)


_ExpansionMode = Literal["none", "replace", "append"]


def _normalize_query(query: str) -> str:
    normalized = " ".join(query.lower().split())
    if not normalized:
        return normalized
    return f" {normalized} "


def _has_agent_session_close_intent(normalized: str) -> bool:
    if not normalized:
        return False
    for cue in _AGENT_SESSION_CLOSE_CUES:
        if cue.startswith(" ") or cue.endswith(" "):
            if cue in normalized:
                return True
        elif cue in normalized.strip():
            return True
    return False


def classify_expansion_mode(query: str) -> _ExpansionMode:
    normalized = _normalize_query(query)
    if not normalized.strip():
        return "none"
    if _has_agent_session_close_intent(normalized):
        return "none"
    if any(cue in normalized for cue in _HIGH_PRECISION_WRITING_CUES):
        return "replace"
    if any(cue in normalized.strip() for cue in _WEAK_WRITING_CUES):
        return "append"
    return "none"


def classify_unscoped_writing_intent(query: str) -> bool:
    """True when the query triggers any writing-intent expansion."""
    return classify_expansion_mode(query) != "none"


def expand_default_scopes_for_query(
    query: str,
    default_scopes: list[str],
) -> tuple[list[str], bool]:
    """Return (scopes, expanded). Only when defaults are exactly ``[\"all\"]``."""
    if default_scopes != ["all"]:
        return default_scopes, False
    mode = classify_expansion_mode(query)
    if mode == "replace":
        return list(_WRITING_RESEARCH_SCOPES), True
    if mode == "append":
        merged: list[str] = ["all"]
        for scope in _WRITING_RESEARCH_SCOPES:
            if scope not in merged:
                merged.append(scope)
        return merged, True
    return default_scopes, False
