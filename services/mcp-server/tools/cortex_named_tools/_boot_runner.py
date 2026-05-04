"""run_cortex_boot orchestrator — coordinates transcript, fetch, render, and return."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from mcp_events import record

from .._boot_helpers import render_briefing_card, render_operational_context
from ._boot_audit_dump import write_audit_dump
from ._boot_data_fetch import _build_futures_spec, _extract_boot_results
from ._boot_manifest import FetchRecorder, InjectedArtifact, serialize_manifest
from ._boot_profiles import _BOOT_PROFILES
from ._boot_summarize import _build_review_top, _build_unread_threads
from ._boot_transcript import _resolve_transcript

_LA = ZoneInfo("America/Los_Angeles")
_OPS_CONTEXT_DIR = Path("/data/files/notes/system/shared")
logger = logging.getLogger(__name__)


class BootMode(Enum):
    LIVE = "live"
    INSPECT = "inspect"


def _build_artifacts(
    *,
    agent: str,
    card: str,
    ops_context: str,
    manifest: list[dict[str, Any]],
    op_ctx_path: str,
    op_ctx_written: bool,
    recorder: FetchRecorder,
) -> list[InjectedArtifact]:
    """Assemble the InjectedArtifact list for a boot.

    Shared between LIVE mode (run_cortex_boot) and INSPECT mode
    (boot_inspect → run_cortex_boot with mode=INSPECT). When op_ctx_written
    is False, the operational_context artifact is recorded as `inline`
    (its bytes are returned to the caller via operational_context_inline,
    not written to disk).

    Ownership notes:

    - `fetches` is snapshotted from `recorder.records` at assembly time
      via `list(recorder.records)`, never aliased. A live-list handoff
      would create ambiguous ownership if the recorder were ever reused
      or extended after assembly.

    - Every entry in `sections_available` becomes one `manifest_only`
      artifact, regardless of hint syntax. The audit contract is "every
      thing an agent can pull on demand is represented"; a tool-prefix
      allowlist would silently drop entries with `cortex(...)`, `rag(...)`,
      REST URIs, or future hint forms.
    """
    artifacts: list[InjectedArtifact] = [
        InjectedArtifact.from_text(
            name="briefing_card",
            mode="inline",
            source="render_briefing_card()",
            text=card,
            fetches=list(recorder.records),  # snapshot, not alias
        ),
        InjectedArtifact.from_text(
            name="operational_context",
            mode="written_file" if op_ctx_written else "inline",
            source=f"render_operational_context(agent={agent!r})",
            text=ops_context,
            path=op_ctx_path if op_ctx_written else None,
        ),
    ]
    # Every sections_available entry → one manifest_only artifact.
    for section in manifest:
        artifacts.append(
            InjectedArtifact(
                name=section["section"],
                mode="manifest_only",
                source=section.get("hint", ""),
                bytes=0,  # not yet fetched
                sha256="",
                fetches=[],
            )
        )
    return artifacts


def run_cortex_boot(
    agent: str = "cursor",
    transcript_id: str = "",
    mode: BootMode = BootMode.LIVE,
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
    session_id = (
        f"{agent}-{t_boot.strftime('%Y-%m-%d-%H%M')}"
        if mode == BootMode.LIVE
        else f"inspect-{agent}-{t_boot.strftime('%Y-%m-%d-%H%M%S')}"
    )
    profile = _BOOT_PROFILES.get(agent, _BOOT_PROFILES["cursor"])

    recorder = FetchRecorder()
    futures_spec = _build_futures_spec(agent, profile, recorder)
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
    if mode == BootMode.LIVE:
        try:
            _OPS_CONTEXT_DIR.mkdir(parents=True, exist_ok=True)
            (_OPS_CONTEXT_DIR / f"operational-context-{agent}.md").write_text(
                ops_context
            )
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

    artifacts = _build_artifacts(
        agent=agent,
        card=card,
        ops_context=ops_context,
        manifest=manifest,
        op_ctx_path=op_ctx_path,
        op_ctx_written=op_ctx_written,
        recorder=recorder,
    )

    audit_dump_path: str | None = None
    if mode == BootMode.LIVE:
        audit_dump_path = write_audit_dump(
            session_id=session_id,
            agent=agent,
            boot_time=t_boot,
            card=card,
            ops_context=ops_context,
            artifacts=artifacts,
            transcript_continuation=tc_summary,
        )

        logger.info(
            "cortex_boot: agent=%s card_size=%d manifest_sections=%d",
            agent,
            len(card),
            len(manifest),
        )
        record("mcp.cortex.boot", agent=agent)
        record(
            "mcp.cortex.boot.manifest.assembled",
            agent=agent,
            artifact_count=len(artifacts),
            total_bytes=sum(a.bytes for a in artifacts if a.bytes >= 0),
        )

    result: dict[str, Any] = {
        "session_id": session_id,
        "mode": mode.value,
        "utc_now": t_boot.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "local_time": t_boot.astimezone(_LA).strftime("%Y-%m-%dT%H:%M:%S%z"),
        "briefing_card": card,
        "sections_available": manifest,
        "operational_context_inline": ops_context if mode == BootMode.INSPECT else None,
        "operational_context_ref": op_ctx_path if op_ctx_written else None,
        "injected_artifacts": serialize_manifest(artifacts),
        "audit_dump_path": audit_dump_path,
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
