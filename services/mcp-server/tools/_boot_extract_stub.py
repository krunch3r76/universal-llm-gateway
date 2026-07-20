"""Minimal extract_boot_results stub for cortex_brief unit tests."""

from __future__ import annotations

from typing import Any


def stub_extract_boot_results(
    _agent: str, _raw: dict[str, Any], _profile: dict[str, Any]
) -> dict[str, Any]:
    """Return the key set ``run_cortex_brief`` indexes after extract."""
    return {
        "sessions": [],
        "continuity": {},
        "deadlines": [],
        "threads": [],
        "unread_toc_threads": [],
        "unread_thread_total": 0,
        "unread_turn_total": 0,
        "unread_window_label": "14d",
        "staging_items": [],
        "todos": [],
        "self_reflections": [],
        "rj_entries": [],
        "rj_total": 0,
        "recent_mentions": [],
        "skills": [],
        "skills_unpartitioned_count": 0,
        "skills_concise_markdown": "",
        "skills_card_markdown": "",
        "plan_phases": [],
        "in_flight_todos": [],
        "open_arcs": [],
        "temporal_active": [],
        "expired_unresolved": [],
        "review_total": 0,
        "audit_counters": None,
        "async_dispatches": [],
        "principal_context": None,
        "cross_domain_sentinel": None,
        "boot_domain": None,
        "rag_pipeline": {},
        "rules": [],
    }
