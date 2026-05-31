"""Briefing card renderer."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from ._briefing_card_render import (
    PREVIEW_MAX_CHARS,
    deadline_line,
    filter_recent_self_reflections,
    render_async_dispatch_section,
    render_audit_alerts_section,
    render_skills_section,
    render_views_section,
    truncate_at_sentence,
)
from ._manifest import build_manifest
from ._orientation_blocks import render_orientation_blocks
from ._time import relative_time

_LA = ZoneInfo("America/Los_Angeles")

# Hard byte ceiling for last-session summary. The truncator seeks the last
# sentence boundary in the back half of the window — avoids the mid-word cut
# that primed confabulation in the canonical claude-web-lead-2026-05-12 boot.
_LAST_SESSION_SUMMARY_MAX = 300
_LAST_SESSION_RECOVERY = "cortex(tool='journal_read', arguments='{\"limit\": 1}')"

# Cap dropbox-pending inline listing to keep the briefing card compact
# (~3-5KB target per render_briefing_card docstring). At HEAD with 254+
# pending files an unbounded dump pushes the card to ~37KB; first-20 +
# count tail mirrors the truncation pattern used elsewhere in this renderer.
_DROPBOX_DISPLAY_MAX = 20


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
    dropbox_files: list[str] | None = None,
    views_data: list[dict[str, Any]] | None = None,
    async_dispatches: list[dict[str, Any]] | None = None,
    audit_counters: dict[str, int] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Render a compact briefing card (~3-5KB) and section manifest.

    Returns (card_markdown, sections_available).
    The card contains only priority signals — enough for the agent to orient
    and decide what to pull deeper. Heavy sections are replaced with counts
    and fetch hints in the manifest.
    """
    now = datetime.now(UTC)
    local_now = now.astimezone(_LA)
    today = local_now.date()
    parts: list[str] = [
        f"# Boot Briefing — {local_now.strftime('%Y-%m-%dT%H:%M:%S%z')}"
    ]

    if transcript_continuation:
        tc = transcript_continuation
        parts.append(f"\n## Resuming From: `{tc.get('entity_id', '?')}`")
        summary = tc.get("summary", tc.get("description", ""))
        if summary:
            parts.append(f"**Summary**: {summary}")

    # Capability-axis Dispatch & Consult + co-located Liveness blocks, emitted
    # ABOVE the skills list (A2). Source: _orientation_blocks (durable home, 2a).
    parts.extend(render_orientation_blocks())

    if skills:
        parts.extend(render_skills_section(skills, skills_unpartitioned_count))

    if dropbox_files:
        n = len(dropbox_files)
        parts.append(f"\n## ⚠ Dropbox Pending ({n} file(s))")
        # Cap inline listing to keep the briefing card compact (~3-5KB target
        # per render_briefing_card docstring). At HEAD with 254+ pending files
        # an unbounded dump pushes the card to ~37KB; first-20 + count tail
        # mirrors the truncation pattern used elsewhere in this renderer.
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
        parts.append(f"\n## Temporally Active ({len(temporal_active)})")
        for a in temporal_active[:5]:
            name = a.get("entity_name", a.get("entity_id", "?"))
            until = a.get("valid_until", "")
            tag = ""
            if until:
                try:
                    exp = datetime.fromisoformat(until.replace("Z", "+00:00")).date()
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

    if last_session:
        agent = last_session.get("agent", "?")
        ts = last_session.get("timestamp", "?")
        rel = relative_time(str(ts), now)
        parts.append(f"\n## Last Session — {agent} ({rel})")
        chain = (
            continuity.get("continuity_chain", [])
            if isinstance(continuity, dict)
            else []
        )
        continuations = (
            continuity.get("continuations", []) if isinstance(continuity, dict) else []
        )
        # Handoffs are user-facing artifacts for manual copy-paste at end of chat;
        # they MUST NOT auto-surface on subsequent boots (per assertion 8384,
        # session web-2026-05-04-1057). The boot card surfaces only the
        # last-session summary; absence of a handoff is not a gap.
        _summary_raw = last_session.get("summary", "No summary.")
        _summary_cut = truncate_at_sentence(_summary_raw, _LAST_SESSION_SUMMARY_MAX)
        if len(_summary_raw) > len(_summary_cut):
            parts.append(
                f"{_summary_cut} "
                f"[+{len(_summary_raw) - len(_summary_cut)} chars truncated — "
                f"`{_LAST_SESSION_RECOVERY}` for full]"
            )
        else:
            parts.append(_summary_cut)
        if chain:
            parts.append("")
            parts.append("**Continuity**")
            if continuations:
                prefix = chain[:-1]
                latest = chain[-1]
                rendered = " → ".join(
                    prefix
                    + [
                        f"[continuations: {', '.join(continuations + [latest])}]",
                        "[you are here]",
                    ]
                )
            else:
                rendered = " → ".join(chain + ["[you are here]"])
            parts.append(rendered)
        open_items = last_session.get("open_items", [])
        if open_items:
            parts.append(f"**Open items** ({len(open_items)}):")
            for item in open_items[:5]:
                parts.append(f"- {item}")
            if len(open_items) > 5:
                parts.append(
                    f"- *…{len(open_items) - 5} more — "
                    f"`{_LAST_SESSION_RECOVERY}` for full list*"
                )

    if plan_phases or in_flight_todos:
        parts.append("\n## Recent Work")
        if plan_phases:
            parts.append("**Plan phases** (most recently active):")
            for p in plan_phases:
                state_tag = "🔄" if p.get("workflow_state") == "in_progress" else "✓"
                name = p.get("name", p.get("id", "?"))
                plan_tag = f" [{p['plan_id']}]" if p.get("plan_id") else ""
                parts.append(f"- {state_tag} `{p.get('id', '?')}`{plan_tag} {name}")
        if in_flight_todos:
            parts.append("**In-flight todos**:")
            for t in in_flight_todos:
                domain_tag = f" [{t['domain']}]" if t.get("domain") else ""
                parts.append(f"- `{t.get('id', '?')}`{domain_tag} {t.get('name', '')}")

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
                claim_preview = truncate_at_sentence(
                    a.get("claim", ""), PREVIEW_MAX_CHARS
                )
                parts.append(f"- {session_tag}{claim_preview}")

    if reflective_entries is not None:
        if reflective_entries:
            parts.append(f"\n## Reflective Journal ({reflective_total} total)")
            for e in reflective_entries[:5]:
                kind = e.get("kind", "entry")
                kind_tag = f" [{kind}]" if kind != "entry" else ""
                register = e.get("register", "?")
                entry_preview = truncate_at_sentence(
                    e.get("entry") or "", PREVIEW_MAX_CHARS
                )
                parts.append(f"- *{register}*{kind_tag}: {entry_preview}")
            if reflective_total > 5:
                parts.append(
                    f"- *…{reflective_total - 5} more — "
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
