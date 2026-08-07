"""Close draft constants and field schema."""

from __future__ import annotations

from typing import Any

SHORT_TTL_DAYS = 7
LONG_STOP_DAYS = 30
UNCOMMITTED_CAP = 20

ALLOWED_FIELD_KEYS = frozenset(
    {
        "summary",
        "session_summary_md",
        "session_summary_md_path",
        "decisions",
        "open_items",
        "entity_ids",
        "reflections",
        "handoff",
        "handoff_source_path",
        "transcript_md_path",
        "depth",
        "domains",
        "prior_session_id",
    }
)

GRAPH_WRITE_KEYS = frozenset(
    {
        "assert",
        "assertion",
        "entity",
        "entity_id",
        "relationship",
        "relationships",
        "assertions",
        "entities",
        "@graph",
    }
)

ReflectionItem = dict[str, Any]

DEFAULT_CHECKLIST: list[dict[str, str]] = [
    {
        "item": "summary",
        "hint": "Short synthesis (≥20 chars) for journal row + entity name.",
    },
    {
        "item": "session_summary_md",
        "hint": "Structural layer with ## Session Summary; path param for big payloads.",
    },
    {
        "item": "decisions",
        "hint": "Settled claims with settler named — optional but recommended.",
    },
    {
        "item": "entity_ids",
        "hint": "Drop open todo: entities before commit; keep [todo:slug] text refs.",
    },
    {
        "item": "depth",
        "hint": "light (default via default_depth_for_agent) | verbatim (requires transcript_md_path) | none.",
    },
    {
        "item": "check",
        "hint": "Run check until PASS before commit.",
    },
]
