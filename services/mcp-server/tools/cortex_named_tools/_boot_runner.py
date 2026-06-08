"""run_cortex_boot orchestrator — coordinates transcript, fetch, render, and return."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from agent_seat.profiles import get_profile, resolve_seat
from mcp_events import record
from universal_logging import get_logger

from .._boot_helpers import render_briefing_card, render_operational_context
from ..filesystem._ops_text import list_files_impl as _list_files
from ._boot_audit_dump import write_audit_dump
from ._boot_data_fetch import build_futures_spec, extract_boot_results
from ._boot_manifest import FetchRecorder, InjectedArtifact, serialize_manifest
from ._boot_summarize import build_review_top, build_unread_threads
from ._boot_transcript import resolve_transcript

_LA = ZoneInfo("America/Los_Angeles")
_OPS_CONTEXT_DIR = Path("/data/files/notes/system/shared")
logger = get_logger(__name__)


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
    (boot_inspect → run_cortex_boot with mode=INSPECT). The
    operational_context artifact is recorded as `written_file` with `path`
    set to the canonical on-disk location regardless of whether THIS boot
    wrote the file. INSPECT does not perform the disk write (preserving
    its no-side-effects contract) but still surfaces the path so callers
    can `fs read` it directly — that file is rewritten by every LIVE boot
    for the same agent.

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
            # Always reported as `written_file` — the path is the contract.
            # LIVE writes; INSPECT relies on prior LIVE having written. The
            # bytes/sha256 still describe THIS boot's render so cross-boot
            # diff stays accurate.
            mode="written_file",
            source=f"render_operational_context(agent={agent!r})",
            text=ops_context,
            path=op_ctx_path,
        ),
    ]
    # `op_ctx_written` is retained on the call surface for callers that need
    # to distinguish "this boot wrote the file" from "the file may already
    # exist on disk"; not currently consumed downstream of artifact assembly.
    _ = op_ctx_written
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


def _materialize_views(
    views: list[str],
) -> list[dict[str, Any]]:
    """Fetch structural subgraph data for each view entity.

    Calls GET /subgraph/render?root=<entity_id>&hops=1 for each view and
    extracts entity_count + edge_count.  All failures degrade gracefully to a
    zero-count entry so the manifest entry still appears (retrieval hint is
    always accurate).  Returns no prose — structural IDs/counts only (§C.3).
    """
    from urllib.parse import urlencode

    from .._cortex_relay import cx

    results: list[dict[str, Any]] = []
    for entity_id in views:
        qs = urlencode({"root": entity_id, "hops": 1})
        resp = cx("GET", f"/subgraph/render?{qs}")
        entity_count = int(resp.get("entity_count", 0)) if "entity_count" in resp else 0
        edge_count = int(resp.get("edge_count", 0)) if "edge_count" in resp else 0
        results.append(
            {
                "entity_id": entity_id,
                "entity_count": entity_count,
                "edge_count": edge_count,
                "retrieval_hint": (
                    "cortex(tool='render_subgraph', arguments='"
                    f'{{"root": "{entity_id}", "hops": 1}}\')'
                ),
            }
        )
    return results


def run_cortex_boot(
    family: str | None = None,
    platform: str | None = None,
    role: str | None = None,
    transcript_id: str = "",
    mode: BootMode = BootMode.LIVE,
    views: list[str] | None = None,
    principal: str | None = None,
) -> dict[str, Any]:
    """Build a Cortex boot briefing for internal callers and MCP.

    Args:
        family       — model family: claude / gpt / grok / gemini (default: claude)
        platform     — platform surface: cursor / api / web (default: cursor)
        role         — optional functional team seat: lead / reviewer / gatherer /
                       synthesizer / artisan / skeptic / investigator (legacy)
        transcript_id — if provided, loads continuation context for that transcript
        mode         — LIVE (default) writes op-context to disk; INSPECT is side-effect-free
        views        — optional list of entity IDs to materialize as subgraph views
                       in the briefing card (structural counts + manifest hints, no prose)
        principal    — optional principal entity_id (e.g. person:kaywan-mansubi).
                       When set, fetches GET /boot-principal-context for the compact
                       head block (fields 1+2). Field 1 renders only when
                       attributes.durable_identity is set on the principal entity.

    Returns a slim briefing card (~25-35KB typical) with a section manifest pointing
    to existing MCP tools for deeper pulls. Heavy data is NOT inlined — pull on demand.
    """
    # Resolve (family, platform); defaults to (claude, cursor) when both are None.
    resolved_family, resolved_platform = resolve_seat(family=family, platform=platform)
    profile = get_profile(resolved_family, resolved_platform)

    # Seat slug for session IDs, op_ctx paths, and events — new {family}-{platform} format.
    seat_slug = f"{resolved_family}-{resolved_platform}"

    transcript_continuation = resolve_transcript(transcript_id)
    _tc_warning: str | None = None
    if transcript_continuation and "error" in transcript_continuation:
        # Forward-reference: the dispatching session is still active and has not
        # yet written its transcript entity to Cortex.  This is expected, not a
        # caller error.  Degrade gracefully — boot proceeds without continuation
        # context, consistent with hydrate_agent() in libs/agent_seat/hydration.py.
        _tc_warning = (
            f"transcript_id {transcript_continuation['transcript_id']!r} was not found in "
            "Cortex. If this ID was supplied by a session that is still active, the entity "
            "will not be committed until that session closes — this is expected. "
            "Boot proceeds without continuation context."
        )
        transcript_continuation = None

    t_boot = datetime.now(UTC)
    session_id = (
        f"{seat_slug}-{t_boot.strftime('%Y-%m-%d-%H%M')}"
        if mode == BootMode.LIVE
        else f"inspect-{seat_slug}-{t_boot.strftime('%Y-%m-%d-%H%M%S')}"
    )
    # Build a profile dict compatible with build_futures_spec / extract_boot_results.
    # These helpers still expect a dict with specific keys; map from CapabilityProfile.
    profile_dict: dict[str, Any] = {
        "include_deadlines": profile.include_deadlines,
        "include_review_queue": profile.include_review_queue,
        "session_limit": profile.session_limit,
        "self_reflections_limit": profile.self_reflections_limit,
        "session_agent_filter": None,
    }
    # Family anchor replaces the old self_entity_id (persona role entity).
    # The boot data fetch uses this to scope self-reflection assertions.
    from agent_seat.profiles import family_anchor, role_anchor

    self_entity_id = family_anchor(resolved_family)
    if role is not None:
        # When a role is supplied, also scope reflections to the role anchor.
        # For now we use the family anchor as primary; role anchor is available
        # for future expansion. Record both in the profile dict.
        profile_dict["role_entity_id"] = role_anchor(role)
    profile_dict["self_entity_id"] = self_entity_id
    if principal:
        profile_dict["principal"] = principal

    recorder = FetchRecorder()
    futures_spec = build_futures_spec(seat_slug, profile_dict, recorder)
    with ThreadPoolExecutor(max_workers=8) as pool:
        submitted = {k: pool.submit(*spec) for k, spec in futures_spec.items()}
        future_to_key = {f: k for k, f in submitted.items()}
        raw = {}
        for future in as_completed(submitted.values()):
            raw[future_to_key[future]] = future.result()

    extracted = extract_boot_results(seat_slug, raw, profile_dict)

    # One canonical file per seat; role affects render inputs only (not path).
    op_ctx_path = f"notes/system/shared/operational-context-{seat_slug}.md"
    ops_context = render_operational_context(
        agent=seat_slug,
        family=resolved_family,
        platform=resolved_platform,
        role=role,
        unread_count=len(extracted["unread_turns"]),
        review_total=extracted["review_total"],
    )
    op_ctx_written = False
    if mode == BootMode.LIVE:
        try:
            _OPS_CONTEXT_DIR.mkdir(parents=True, exist_ok=True)
            (_OPS_CONTEXT_DIR / f"operational-context-{seat_slug}.md").write_text(
                ops_context
            )
            op_ctx_written = True
        except OSError as exc:
            logger.warning(
                "Could not write operational context to %s: %s", op_ctx_path, exc
            )
            record(
                "mcp.cortex.boot.opcontext.writefailed",
                agent=seat_slug,
                path=op_ctx_path,
                error=str(exc),
            )

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
            # Verification status of the handoff this session is resuming FROM.
            # Surfaced as a flag-only caution on the boot card (no prose inlined,
            # preserving decision 8384). build_handoff_surface returns None when
            # the transcript carries no handoff_prompt.
            "handoff_surface": tc.get("handoff_surface"),
        }

    unread_threads = build_unread_threads(extracted["threads"])
    review_top = build_review_top(extracted["staging_items"])

    dropbox_files: list[str] = _list_files("dropbox/").get("files", [])

    views_data: list[dict[str, Any]] = _materialize_views(views) if views else []

    card, manifest = render_briefing_card(
        deadlines=extracted["deadlines"]
        if profile_dict.get("include_deadlines", True)
        else None,
        unread_count=len(extracted["unread_turns"]),
        unread_threads=unread_threads,
        review_total=extracted["review_total"],
        review_top=review_top,
        last_session=(extracted.get("continuity") or {}).get("last_session")
        or (extracted["sessions"][0] if extracted["sessions"] else None),
        continuity=extracted.get("continuity") or None,
        self_reflections=extracted["self_reflections"] or None,
        todos=extracted["todos"] or None,
        todo_total=len(extracted["todos"]),
        temporal_active=extracted["temporal_active"] or None,
        # Expired-unresolved is intentionally NOT rendered on the briefing card.
        # The bucket is dominated by stale temporal-ledger rows (Chase statement
        # periods, PG&E billing cycles) that aged past `valid_until` but have no
        # corresponding "resolution event" — the supersede footer the section
        # used to render assumed an action that doesn't exist for billing
        # cycles. Forensic access still works via the temporal endpoint
        # directly: `cortex GET /boot-temporal` returns the bucket whether or
        # not the briefing renders it.
        expired_unresolved=None,
        transcript_continuation=tc_summary,
        reflective_entries=extracted["rj_entries"],
        reflective_total=extracted["rj_total"],
        recent_mentions=extracted["recent_mentions"] or None,
        skills=extracted["skills"] or None,
        skills_unpartitioned_count=extracted.get("skills_unpartitioned_count", 0),
        plan_phases=extracted["plan_phases"] or None,
        in_flight_todos=extracted["in_flight_todos"] or None,
        open_arcs=extracted.get("open_arcs") or None,
        dropbox_files=dropbox_files or None,
        views_data=views_data or None,
        async_dispatches=extracted.get("async_dispatches") or None,
        audit_counters=extracted.get("audit_counters") or None,
        # Surface-aware dispatch block: grok → flat direct-call; claude/gpt/gemini
        # → dispatch-route (OVERFLOW). See _orientation_blocks (thread 1167).
        family=resolved_family,
        agent=seat_slug,
        principal_context=extracted.get("principal_context") or None,
    )

    artifacts = _build_artifacts(
        agent=seat_slug,
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
            agent=seat_slug,
            boot_time=t_boot,
            card=card,
            ops_context=ops_context,
            artifacts=artifacts,
            transcript_continuation=tc_summary,
        )

        logger.info(
            "cortex_boot: seat=%s card_size=%d manifest_sections=%d",
            seat_slug,
            len(card),
            len(manifest),
        )
        record("mcp.cortex.boot", agent=seat_slug)
        record(
            "mcp.cortex.boot.manifest.assembled",
            agent=seat_slug,
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
        # Path is now the contract for both modes. LIVE writes the file;
        # INSPECT reads what LIVE wrote (or accepts a 404 if no LIVE boot
        # for this agent has run yet — acceptable degradation, the content
        # is deterministic from the renderer at the same git rev).
        "operational_context_ref": op_ctx_path,
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

    if _tc_warning:
        result["transcript_id_note"] = _tc_warning

    return result
