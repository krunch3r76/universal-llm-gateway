"""Briefing card renderer."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from ._briefing_card_render import (
    _NOTES_PREVIEW_MAX_CHARS,
    PREVIEW_MAX_CHARS,
    deadline_line,
    filter_recent_self_reflections,
    render_arc_section,
    render_async_dispatch_section,
    render_audit_alerts_section,
    render_compact_block,
    render_skills_section,
    render_views_section,
    truncate_at_sentence,
)
from ._manifest import build_manifest
from ._orientation_blocks import render_orientation_blocks
from ._time import relative_time

_LA = ZoneInfo("America/Los_Angeles")

# Cap dropbox-pending inline listing. At HEAD with 250+ pending files an
# unbounded dump pushes the card to ~37KB. First-N + count tail mirrors
# the truncation pattern used elsewhere in this renderer.
_DROPBOX_DISPLAY_MAX = 3


def _tokenize_signal_text(text: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", text.lower()) if len(t) > 2}


def _collect_boot_signals(
    *,
    todos: list[dict[str, Any]] | None,
    unread_threads: list[dict[str, Any]] | None,
    open_arcs: list[dict[str, Any]] | None,
    in_flight_todos: list[dict[str, Any]] | None,
) -> set[str]:
    """Lowercased session tokens for Tier-2 skill ranking."""
    signals: set[str] = set()
    for todo in todos or []:
        for field in ("domain", "title"):
            val = todo.get(field)
            if val:
                signals |= _tokenize_signal_text(str(val))
        eid = str(todo.get("id") or "")
        if eid:
            signals |= _tokenize_signal_text(eid)
            if ":" in eid:
                signals |= _tokenize_signal_text(eid.split(":", 1)[1])
    for thread in unread_threads or []:
        for field in ("slug", "subject", "last_subject"):
            val = thread.get(field)
            if val:
                signals |= _tokenize_signal_text(str(val))
    for arc in open_arcs or []:
        eid = str(arc.get("id") or "")
        if eid:
            signals |= _tokenize_signal_text(eid)
            if ":" in eid:
                signals |= _tokenize_signal_text(eid.split(":", 1)[1])
        for child in arc.get("children") or []:
            cid = str(child.get("id") or "")
            if cid:
                signals |= _tokenize_signal_text(cid)
                if ":" in cid:
                    signals |= _tokenize_signal_text(cid.split(":", 1)[1])
    for todo in in_flight_todos or []:
        eid = str(todo.get("id") or "")
        if eid:
            signals |= _tokenize_signal_text(eid)
            if ":" in eid:
                signals |= _tokenize_signal_text(eid.split(":", 1)[1])
    return signals


def render_briefing_card(
    *,
    deadlines: list[dict[str, Any]] | None = None,
    unread_count: int = 0,
    unread_threads: list[dict[str, Any]] | None = None,
    review_total: int | None = None,
    review_top: list[dict[str, Any]] | None = None,
    last_session: dict[str, Any] | None = None,
    continuity: dict[str, Any] | None = None,
    self_reflections: list[dict[str, Any]] | None = None,
    todos: list[dict[str, Any]] | None = None,
    todo_total: int = 0,
    temporal_active: list[dict[str, Any]] | None = None,
    expired_unresolved: list[dict[str, Any]] | None = None,
    transcript_continuation: dict[str, Any] | None = None,
    reflective_entries: list[dict[str, Any]] | None = None,
    reflective_total: int = 0,
    recent_mentions: list[dict[str, Any]] | None = None,
    recent_mentions_window_days: int = 7,
    skills: list[dict[str, Any]] | None = None,
    skills_unpartitioned_count: int = 0,
    plan_phases: list[dict[str, Any]] | None = None,
    in_flight_todos: list[dict[str, Any]] | None = None,
    open_arcs: list[dict[str, Any]] | None = None,
    dropbox_files: list[str] | None = None,
    views_data: list[dict[str, Any]] | None = None,
    async_dispatches: list[dict[str, Any]] | None = None,
    audit_counters: dict[str, int] | None = None,
    principal_context: dict[str, Any] | None = None,
    family: str | None = None,
    agent: str | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Render a compact briefing card and section manifest.

    Returns (card_markdown, sections_available).
    Priority signals and indexed agent skills compose the card; other heavy
    sections stay as counts + fetch hints in the manifest per
    decision:boot-manifest-mode.

    Per-seat delivered-card byte ceilings are enforced by the caller
    (_boot_runner: on-card breach line + mcp.cortex.boot.card.overbudget
    event); a per-block byte ledger lands in every boot audit dump. Content
    that is session-stable, reference-only, or recoverable on demand belongs
    in the manifest or a fetchable skill/doc, not inline.
    """
    now = datetime.now(UTC)
    local_now = now.astimezone(_LA)
    today = local_now.date()
    parts: list[str] = [
        f"# Boot Briefing — {local_now.strftime('%Y-%m-%dT%H:%M:%S%z')}"
    ]

    compact_dedup_ids: set[int] = set()
    if principal_context:
        compact_lines, compact_dedup_ids = render_compact_block(
            principal_name=str(
                principal_context.get("principal_name")
                or principal_context.get("principal_id", "?")
            ),
            durable_identity=principal_context.get("durable_identity"),
            active_matters=list(principal_context.get("active_matters") or []),
            today=now,
        )
        parts.extend(compact_lines)

    if transcript_continuation:
        tc = transcript_continuation
        entity_id = tc.get("entity_id", "?")
        parts.append(f"\n## Resuming From: `{entity_id}`")
        # Flag-only handoff-verification caution. The agent resuming from a
        # detached_string / unverified handoff otherwise gets no boot-time signal
        # that the prompt was not lead-authored-file-derived (caught this arc only
        # by manual source-fetch). We surface the flag + derivation + the standing
        # re-derive rule, NOT the handoff prose — prose stays suppressed per
        # decision 8384 (handoffs are end-of-chat copy-paste artifacts, not boot
        # auto-surfaces). The status data is on the transcript entity's
        # `handoff_surface` attribute (libs/cortex_store/handoff_surface.py).
        surface = tc.get("handoff_surface")
        verification = None
        if isinstance(surface, dict):
            verification = surface.get("handoff_verification")
        if verification is None:
            verification = tc.get("handoff_verification")
        if isinstance(verification, dict) and verification.get("total"):
            passed = int(verification.get("passed", 0))
            total = int(verification.get("total", 0))
            parts.append(f"> Handoff: verification {passed}/{total} passed.")
            for check in verification.get("checks", []):
                if not isinstance(check, dict):
                    continue
                if check.get("name") == "cited_entity_state_snapshot":
                    detail = str(check.get("detail", ""))
                    if detail and detail != "no cited entities":
                        parts.append(f"> cited: {detail}")
            if passed < total:
                flag = "UNVERIFIED"
                derivation = "?"
                if isinstance(surface, dict):
                    flag = str(surface.get("flag", "unverified")).upper()
                    derivation = surface.get("derivation", "?")
                parts.append(
                    f"> ⚠ **Handoff {flag}** (derivation={derivation}) — "
                    "re-derive failed checks before acting; no confirmation "
                    "writes until the handoff is reviewed or falsified. "
                    "Prose intentionally not inlined (decision 8384); pull "
                    "the flagged surface via "
                    f"`cortex(tool='entity_get', arguments='{{\"entity_id\": "
                    f'"{entity_id}"}}\')`.'
                )
        elif (
            isinstance(surface, dict)
            and surface.get("surfaced")
            and not surface.get("verified")
        ):
            flag = str(surface.get("flag", "unverified")).upper()
            derivation = surface.get("derivation", "?")
            parts.append(
                f"> ⚠ **Handoff {flag}** (derivation={derivation}) — re-derive "
                "from source before acting; no confirmation writes until the "
                "handoff is reviewed or falsified. Prose intentionally not "
                "inlined (decision 8384); pull the flagged surface via "
                f"`cortex(tool='entity_get', arguments='{{\"entity_id\": "
                f'"{entity_id}"}}\')`.'
            )
        summary = tc.get("summary", tc.get("description", ""))
        if summary:
            parts.append(f"**Summary**: {summary}")

    # Capability-axis Dispatch & Consult + co-located Liveness blocks, emitted
    # ABOVE the skills list (A2). Source: _orientation_blocks (durable home, 2a).
    # Surface-aware: grok seats get the flat direct-call form; claude/gpt/gemini
    # get the dispatch-route (OVERFLOW) form (thread 1167, 2026-06-01).
    parts.extend(render_orientation_blocks(family=family, agent=agent))

    # Arc digest — been → are → going (directive 3 / assertion 13717). Absorbs
    # the former Last Session + Continuity + Open arcs + Recent Work blocks;
    # all inputs are params already fetched for this render (zero new fetches).
    parts.extend(
        render_arc_section(
            continuity=continuity if isinstance(continuity, dict) else None,
            last_session=last_session,
            open_arcs=open_arcs,
            in_flight_todos=in_flight_todos,
            deadlines=deadlines,
            now=now,
        )
    )

    if skills:
        boot_signals = _collect_boot_signals(
            todos=todos,
            unread_threads=unread_threads,
            open_arcs=open_arcs,
            in_flight_todos=in_flight_todos,
        )
        parts.extend(
            render_skills_section(skills, skills_unpartitioned_count, boot_signals)
        )

    if dropbox_files:
        n = len(dropbox_files)
        parts.append(f"\n## ⚠ Dropbox Pending ({n} file(s))")
        # Cap inline listing per the ≤~8KB card target (render_briefing_card
        # docstring). At HEAD with 250+ pending files an unbounded dump pushes
        # the card to ~37KB; first-N + count tail mirrors the truncation
        # pattern used elsewhere in this renderer.
        for f in dropbox_files[:_DROPBOX_DISPLAY_MAX]:
            parts.append(f"  {f}")
        if n > _DROPBOX_DISPLAY_MAX:
            parts.append(
                f"  *…{n - _DROPBOX_DISPLAY_MAX} more — "
                "see /data/files/dropbox/ for full listing*"
            )
        parts.append(
            "→ Read agent-skills/document-lifecycle-tracking.md before handling"
            " — dropbox ingest required."
        )

    if deadlines is not None:
        # Drop rows without a real deadline date — they carry no urgency signal.
        dated = [
            d
            for d in deadlines
            if d.get("deadline_date") and str(d.get("deadline_date")).lower() != "none"
        ]
        parts.append("\n## Deadlines")
        if not dated:
            parts.append("No active deadlines.")
        else:
            for d in dated:
                parts.append(deadline_line(d, now))

    if expired_unresolved:
        parts.append(f"\n## Expired — Action Needed ({len(expired_unresolved)})")
        for a in expired_unresolved[:5]:
            name = a.get("entity_name", a.get("entity_id", "?"))
            until = a.get("valid_until", "")
            days_tag = ""
            if until:
                try:
                    exp = datetime.fromisoformat(until.replace("Z", "+00:00")).date()
                    days_past = (today - exp).days
                    days_tag = f" (expired {days_past}d ago)"
                except (ValueError, TypeError):
                    pass
            claim_preview = (a.get("claim") or "")[:100]
            aid = a.get("id", "?")
            parts.append(f'- **{name}** [id={aid}]{days_tag} — "{claim_preview}"')
        parts.append(
            "  → If resolved: "
            '`cortex(tool="supersede", '
            "arguments='{\"old_assertion_id\": <id>, ...}')`"
        )

    if temporal_active:
        scoped_active = (
            [a for a in temporal_active if a.get("id") not in compact_dedup_ids]
            if compact_dedup_ids
            else temporal_active
        )
        # One slot per entity: sibling snapshots (e.g. cumulative YTD rows never
        # superseded upstream) otherwise crowd the 5-slot window. Presentation
        # policy only — upstream non-supersession tracked separately (1427 F5).
        by_entity: dict[str, dict[str, Any]] = {}
        for a in scoped_active:
            key = str(a.get("entity_name", a.get("entity_id", "?")))
            prev = by_entity.get(key)
            if prev is None or int(a.get("id") or 0) > int(prev.get("id") or 0):
                by_entity[key] = a
        scoped_active = list(by_entity.values())
        if scoped_active:
            parts.append(f"\n## Temporally Active ({len(scoped_active)})")
            for a in scoped_active[:5]:
                name = a.get("entity_name", a.get("entity_id", "?"))
                until = a.get("valid_until", "")
                tag = ""
                if until:
                    try:
                        exp = datetime.fromisoformat(
                            until.replace("Z", "+00:00")
                        ).date()
                        delta = (exp - today).days
                        if delta == 0:
                            tag = " (expires today)"
                        elif delta > 0:
                            tag = f" (expires in {delta}d)"
                        else:
                            tag = f" (**expired {abs(delta)}d ago**)"
                    except (ValueError, TypeError):
                        pass
                parts.append(f"- **{name}**{tag} — {a.get('claim', '')[:120]}")

    if unread_count > 0:
        _uthreads = unread_threads or []
        thread_slugs = ", ".join(t.get("slug", t.get("id", "?")) for t in _uthreads)
        _thread_count = len(_uthreads)
        # Show both metrics when they differ (turns vs threads) — header/body
        # mismatch caused confabulation in boot audit claude-web-lead-2026-05-12.
        if _thread_count and _thread_count != unread_count:
            _count_label = (
                f"{_thread_count} thread(s) with unread ({unread_count} turn(s))"
            )
        else:
            _count_label = f"{unread_count} unread"
        parts.append(f"\n## Agent Bus — {_count_label}")
        if thread_slugs:
            parts.append(f"Threads with unread: {thread_slugs}")

    if review_total is not None and review_total > 0:
        parts.append(f"\n## Review Queue — {review_total} item(s)")
        for item in (review_top or [])[:3]:
            reason = item.get("reason", "")
            name = item.get("name", item.get("id", "?"))
            parts.append(f"- [{reason}] {name}")

    if todos:
        parts.append(f"\n## Todos — {todo_total} open")
        for t in todos[:5]:
            priority = t.get("priority", "")
            p_tag = f" [{priority}]" if priority else ""
            parts.append(f"- `{t.get('id', '?')}`{p_tag} {t.get('title', '')}")
        if todo_total > 5:
            parts.append(
                f"- *…{todo_total - 5} more — "
                "`cortex(tool='todo_candidates', arguments='{\"query\": \"<intent>\"}')`*"
            )

    if recent_mentions:
        parts.append(
            f"\n## Recent Mentions — trailing {recent_mentions_window_days}d "
            f"({len(recent_mentions)})"
        )
        # ∀ entity listed: retrieve before making claims — name alone primes
        # inference without grounding (boot audit claude-web-lead-2026-05-12).
        parts.append(
            "*Entities with new assertions or newly created — "
            "retrieve before making claims*"
        )
        for m in recent_mentions[:10]:
            name = m.get("entity_name", m.get("entity_id", "?"))
            etype = m.get("entity_type", "?")
            entity_id = m.get("entity_id", "")
            cnt = m.get("inserted_count", 0)
            last_mentioned = m.get("last_mentioned_at")
            rel = relative_time(last_mentioned, now) if last_mentioned else "?"
            cnt_tag = f", {cnt} new" if cnt else ", new entity"
            id_tag = f" `{entity_id}`" if entity_id else ""
            parts.append(f"- **{name}**{id_tag} ({etype}) — {rel}{cnt_tag}")

    if self_reflections:
        recent_reflections = filter_recent_self_reflections(self_reflections, now)
        if recent_reflections:
            parts.append(f"\n## Your Notes ({len(recent_reflections)})")
            for a in recent_reflections:
                # Compact projection ships a pre-extracted `session_tag`
                # (e.g. "web-2026-04-30-0528") so the briefing can render the
                # "[...]" prefix without carrying the full `evidence` payload.
                # Fall back to parsing evidence for callers still using the
                # non-compact shape.
                tag = a.get("session_tag") or ""
                if not tag:
                    evidence = a.get("evidence", "") or ""
                    m = re.search(
                        r"(cursor|web|api|bard)-\d{4}-\d{2}-\d{2}-\d{4}",
                        evidence,
                    )
                    if m:
                        tag = m.group()
                session_tag = f"[{tag}] " if tag else ""
                claim_raw = a.get("claim", "") or ""
                claim_preview = truncate_at_sentence(
                    claim_raw, _NOTES_PREVIEW_MAX_CHARS
                )
                handle = ""
                if len(claim_preview) < len(claim_raw):
                    aid = a.get("id")
                    aid_part = f"a{aid} " if aid is not None else ""
                    handle = f" [{aid_part}+{len(claim_raw) - len(claim_preview)}ch]"
                parts.append(f"- {session_tag}{claim_preview}{handle}")
            parts.append(
                "  *full text by id: `cortex(tool='assertions', "
                f'arguments=\'{{"entity_id": "family:{family or "claude"}"}}\')`*'
            )

    if reflective_entries is not None:
        if reflective_entries:
            parts.append(f"\n## Reflective Journal ({reflective_total} total)")
            for e in reflective_entries[:3]:
                kind = e.get("kind", "entry")
                kind_tag = f" [{kind}]" if kind != "entry" else ""
                register = e.get("register", "?")
                entry_preview = truncate_at_sentence(
                    e.get("entry") or "", PREVIEW_MAX_CHARS
                )
                parts.append(f"- *{register}*{kind_tag}: {entry_preview}")
            if reflective_total > 3:
                parts.append(
                    f"- *…{reflective_total - 3} more — "
                    "`cortex(tool='rj_list', arguments='{\"limit\": 20}')`*"
                )
        else:
            # ∃! case: fetch ran but returned 0 rows for this agent slug.
            # Fresh seats are common during naming-cleanup phases — surface
            # rather than hiding so agents don't read silence as "nothing to
            # report" (boot audit claude-web-lead-2026-05-12).
            parts.append(
                "\n## Reflective Journal\n"
                "No prior reflective journal for this agent slug — fresh seat. "
                "`cortex(tool='rj_list', arguments='{\"limit\": 5}')` to confirm."
            )

    if views_data:
        parts.extend(render_views_section(views_data))

    if async_dispatches:
        parts.extend(render_async_dispatch_section(async_dispatches))

    if audit_counters and audit_counters.get("criticals", 0) > 0:
        parts.extend(render_audit_alerts_section(audit_counters))

    card = "\n".join(parts)
    manifest = build_manifest(
        plan_phases=plan_phases,
        in_flight_todos=in_flight_todos,
        open_arcs=open_arcs,
        todo_total=todo_total,
        unread_count=unread_count,
        reflective_total=reflective_total,
        recent_mentions=recent_mentions,
        skills=skills,
        continuity=continuity,
        views_data=views_data,
        async_dispatches=async_dispatches,
        audit_counters=audit_counters,
    )
    return card, manifest
