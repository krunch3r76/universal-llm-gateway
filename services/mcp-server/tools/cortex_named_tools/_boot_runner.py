"""run_cortex_boot orchestrator — coordinates transcript, fetch, render, and return."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from mcp_events import record

from .._boot_helpers import render_briefing_card, render_operational_context
from ._boot_data_fetch import _build_futures_spec, _extract_boot_results
from ._boot_profiles import _BOOT_PROFILES
from ._boot_summarize import _build_review_top, _build_unread_threads
from ._boot_transcript import _resolve_transcript

_LA = ZoneInfo("America/Los_Angeles")
logger = logging.getLogger(__name__)


def run_cortex_boot(
    agent: str = "cursor",
    transcript_id: str = "",
) -> dict[str, Any]:
    """Build a persona-scoped Cortex boot briefing for internal callers and MCP.

    Returns a slim briefing card (~5-10KB) with a section manifest pointing to
    existing MCP tools for deeper pulls. Heavy data (full sessions, assertions,
    gated entities, legal contacts, file contents) is NOT inlined — agents pull
    on demand via the manifest hints.
    """
    transcript_continuation = _resolve_transcript(transcript_id)
    if transcript_continuation and "error" in transcript_continuation:
        return transcript_continuation

    t_boot = datetime.now(UTC)
    session_id = f"{agent}-{t_boot.strftime('%Y-%m-%d-%H%M')}"
    profile = _BOOT_PROFILES.get(agent, _BOOT_PROFILES["cursor"])

    futures_spec = _build_futures_spec(agent, profile)
    with ThreadPoolExecutor(max_workers=8) as pool:
        submitted = {k: pool.submit(*spec) for k, spec in futures_spec.items()}
        future_to_key = {f: k for k, f in submitted.items()}
        raw = {}
        for future in as_completed(submitted.values()):
            raw[future_to_key[future]] = future.result()

    extracted = _extract_boot_results(agent, raw, profile)

    op_ctx_path = f"notes/system/shared/operational-context-{agent}.md"
    ops_context = render_operational_context(
        agent=agent,
        unread_count=len(extracted["unread_turns"]),
        review_total=extracted["review_total"],
    )
    op_ctx_written = False
    try:
        _op_dir = Path("/data/files/notes/system/shared")
        _op_dir.mkdir(parents=True, exist_ok=True)
        (_op_dir / f"operational-context-{agent}.md").write_text(ops_context)
        op_ctx_written = True
    except OSError:
        logger.warning("Could not write operational context to %s", op_ctx_path)

    tc_summary: dict[str, Any] | None = None
    if transcript_continuation:
        tc = transcript_continuation
        summary = tc.get("description", "")
        if not summary and tc.get("assertions"):
            active = [a for a in tc["assertions"] if not a.get("superseded_by")]
            if active:
                summary = active[0].get("claim", "")
        tc_summary = {
            "entity_id": tc["entity_id"],
            "summary": summary,
        }

    unread_threads = _build_unread_threads(extracted["threads"])
    review_top = _build_review_top(extracted["staging_items"])

    card, manifest = render_briefing_card(
        deadlines=extracted["deadlines"]
        if profile.get("include_deadlines", True)
        else None,
        unread_count=len(extracted["unread_turns"]),
        unread_threads=unread_threads,
        review_total=extracted["review_total"],
        review_top=review_top,
        last_session=extracted["sessions"][0] if extracted["sessions"] else None,
        self_reflections=extracted["self_reflections"] or None,
        todos=extracted["todos"] or None,
        todo_total=len(extracted["todos"]),
        temporal_active=extracted["temporal_active"] or None,
        expired_unresolved=extracted["expired_unresolved"] or None,
        transcript_continuation=tc_summary,
        op_ctx_path=op_ctx_path,
        reflective_entries=extracted["rj_entries"] or None,
        reflective_total=extracted["rj_total"],
        recent_mentions=extracted["recent_mentions"] or None,
        skills=extracted["skills"] or None,
        plan_phases=extracted["plan_phases"] or None,
        in_flight_todos=extracted["in_flight_todos"] or None,
        rag_state=extracted.get("rag_pipeline") or None,
    )

    logger.info(
        "cortex_boot: agent=%s card_size=%d manifest_sections=%d",
        agent,
        len(card),
        len(manifest),
    )
    record("mcp.cortex.boot", agent=agent)

    result: dict[str, Any] = {
        "session_id": session_id,
        "utc_now": t_boot.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "local_time": t_boot.astimezone(_LA).strftime("%Y-%m-%dT%H:%M:%S%z"),
        "briefing_card": card,
        "sections_available": manifest,
        "operational_context_ref": op_ctx_path if op_ctx_written else None,
    }

    if tc_summary:
        result["continuation_transcript"] = {
            **tc_summary,
            "fetch_hint": (
                f"cortex(tool='entity_get', "
                f'arguments=\'{{"entity_id": "{tc_summary["entity_id"]}"}}\')'
            ),
        }

    return result
