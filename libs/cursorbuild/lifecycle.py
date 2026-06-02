"""Boot/close hooks bridging cursorbuild dispatches to the cortex session protocol."""

from __future__ import annotations

from typing import Any

from cursorbuild.runner_types import RunnerResult


def enrich_system_context_for_boot(
    system_context: str | None,
    *,
    agent: str,
    dispatch_id: str,
) -> str | None:
    """Prepend a cortex boot directive for dispatched cursor-agent sessions."""
    directive = (
        f"[cursorbuild dispatch_id={dispatch_id}]\n"
        f"Before substantive work: run cortex_boot(agent={agent!r}) or the "
        "equivalent boot ritual for your seat. On session end: call "
        "cortex(tool='session_close', ...) per session-close.mdc.\n"
    )
    if system_context:
        return f"{directive}\n{system_context}"
    return directive


def session_close_kwargs_from_dispatch(
    *,
    session_id: str,
    agent: str,
    family: str,
    dispatch_id: str,
    rr: RunnerResult,
    summary: str,
    session_summary_md: str,
) -> dict[str, Any]:
    """Build kwargs for ``cortex(tool='session_close', ...)`` after a dispatch."""
    resolved = rr.resolved_session_id or session_id
    return {
        "session_id": resolved or session_id,
        "agent": agent,
        "family": family,
        "transcript_depth": "light",
        "session_summary_md": session_summary_md,
        "summary": summary,
        "entity_ids": [f"dispatch:{dispatch_id}"],
        "open_items": [],
        "decisions": [],
    }
