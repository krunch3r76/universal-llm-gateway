"""Briefing card renderer."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from ._manifest import _build_manifest
from ._time import _relative_time

_LA = ZoneInfo("America/Los_Angeles")

# Reflective Journal / Your Notes preview length. Cap is the hard byte ceiling;
# the truncator prefers the last sentence boundary at-or-before the cap so the
# preview doesn't chop mid-sentence ("If I sit with —" was the canonical bug).
_PREVIEW_MAX_CHARS = 200
# Self-reflection recency cap. Older notes drift out of the boot card — agents
# can re-fetch via /assertions if they're chasing a specific historical claim.
_SELF_REFLECTION_MAX_AGE_DAYS = 14


def _truncate_at_sentence(text: str, max_chars: int) -> str:
    """Truncate `text` at the last sentence boundary at-or-before `max_chars`.

    Sentence boundaries: '. ', '! ', '? ', or terminal '.'/'!'/'?' at the cap.
    Falls back to a hard-cut + ellipsis when no boundary is found in the
    second half of the window — short fragments stay intact, long unbroken
    prose still gets a clean visual cutoff.
    """
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    window = text[:max_chars]
    # Search for sentence terminators in the back half of the window so a
    # period in the first 20 chars doesn't truncate aggressively.
    cutoff_floor = max_chars // 2
    best = -1
    for marker in (". ", "! ", "? "):
        idx = window.rfind(marker)
        if idx >= cutoff_floor and idx + len(marker) > best:
            best = idx + len(marker.rstrip())
    if best > 0:
        return text[:best].rstrip()
    return window.rstrip() + "…"


def _filter_recent_self_reflections(
    self_reflections: list[dict[str, Any]],
    now: datetime,
    *,
    max_age_days: int = _SELF_REFLECTION_MAX_AGE_DAYS,
) -> list[dict[str, Any]]:
    """Drop self-reflections older than `max_age_days` based on created_at.

    The fetcher already orders DESC by created_at; this is a recency cap on
    top of the fixed limit (default 5). When the agent has fewer than 5
    recent reflections, the section degrades naturally — no padding with
    stale entries.
    """
    if not self_reflections:
        return []
    threshold = now - timedelta(days=max_age_days)
    fresh: list[dict[str, Any]] = []
    for a in self_reflections:
        created = a.get("created_at") or a.get("observed_at") or ""
        if not created:
            # No timestamp — keep it; better to render than silently drop.
            fresh.append(a)
            continue
        try:
            ts = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
        except ValueError:
            fresh.append(a)
            continue
        if ts >= threshold:
            fresh.append(a)
    return fresh


def _deadline_line(d: dict[str, Any], today: datetime) -> str:
    """Render a single deadline as a compact markdown line."""
    dl_date = d.get("deadline_date", "")
    remaining = ""
    if dl_date:
        try:
            dl = datetime.strptime(dl_date[:10], "%Y-%m-%d").date()
            delta = (dl - today.date()).days
            if delta >= 0:
                remaining = f" ({delta}d)"
            else:
                remaining = f" (**{abs(delta)}d OVERDUE**)"
        except ValueError:
            pass
    return (
        f"- **{dl_date}**{remaining} — "
        f"{d.get('deadline_name', '')} ({d.get('matter_name', '')})"
    )


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

    if skills:
        parts.append(
            "\n## Agent Skills "
            "(read on trigger match — "
            "`fs(sandbox='cortex', op='read', "
            "path='agent-skills/<NAME>.md')`)"
        )
        for s in skills:
            slug = s.get("name") or (s.get("id") or "?").removeprefix("agent_skill:")
            # Prefer the API-side projection (`/boot-skills` ships
            # `description_first_sentence`); fall back to first-sentence split
            # over a full `description` field for any caller still wiring the
            # legacy `/entities?type=agent_skill` shape through.
            short = s.get("description_first_sentence")
            if not short:
                full = (s.get("description") or "").strip()
                short = full.split(". ", 1)[0].rstrip(".")
            parts.append(f"- **{slug}** — {short}")
        # Drift reminder: surface skills missing `applicable_agents` so the
        # partition script (`scripts/cortex/backfill_agent_skill_applicability.py`)
        # doesn't go stale silently as new and temp skills land. Silent when 0.
        if skills_unpartitioned_count:
            parts.append(
                f"\n> **Skill partition drift**: {skills_unpartitioned_count} "
                f"skill(s) missing `applicable_agents` (default to universal "
                f"via COALESCE). Audit: `scripts/cortex/"
                f"backfill_agent_skill_applicability.py --audit`."
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
                parts.append(_deadline_line(d, now))

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
        thread_slugs = ", ".join(
            t.get("slug", t.get("id", "?")) for t in (unread_threads or [])
        )
        parts.append(f"\n## Agent Bus — {unread_count} unread")
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
        rel = _relative_time(str(ts), now)
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
        parts.append(last_session.get("summary", "No summary.")[:300])
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
                parts.append(f"- *…{len(open_items) - 5} more*")

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
        parts.append(
            "*Entities with new assertions or newly created — recognize these names*"
        )
        for m in recent_mentions[:10]:
            name = m.get("entity_name", m.get("entity_id", "?"))
            etype = m.get("entity_type", "?")
            cnt = m.get("recent_mention_count", 0)
            last_mentioned = m.get("last_mentioned_at")
            rel = _relative_time(last_mentioned, now) if last_mentioned else "?"
            cnt_tag = f", {cnt} new" if cnt else ", new entity"
            parts.append(f"- **{name}** ({etype}) — {rel}{cnt_tag}")

    if self_reflections:
        recent_reflections = _filter_recent_self_reflections(self_reflections, now)
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
                claim_preview = _truncate_at_sentence(
                    a.get("claim", ""), _PREVIEW_MAX_CHARS
                )
                parts.append(f"- {session_tag}{claim_preview}")

    if reflective_entries:
        parts.append(f"\n## Reflective Journal ({reflective_total} total)")
        for e in reflective_entries[:5]:
            kind = e.get("kind", "entry")
            kind_tag = f" [{kind}]" if kind != "entry" else ""
            register = e.get("register", "?")
            entry_preview = _truncate_at_sentence(
                e.get("entry") or "", _PREVIEW_MAX_CHARS
            )
            parts.append(f"- *{register}*{kind_tag}: {entry_preview}")
        if reflective_total > 5:
            parts.append(
                f"- *…{reflective_total - 5} more — "
                "`cortex(tool='rj_list', arguments='{\"limit\": 20}')`*"
            )

    card = "\n".join(parts)
    manifest = _build_manifest(
        plan_phases=plan_phases,
        in_flight_todos=in_flight_todos,
        todo_total=todo_total,
        unread_count=unread_count,
        reflective_total=reflective_total,
        recent_mentions=recent_mentions,
        skills=skills,
        continuity=continuity,
    )
    return card, manifest
