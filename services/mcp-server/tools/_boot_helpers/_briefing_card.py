"""Briefing card renderer."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from ._manifest import _build_manifest
from ._time import _relative_time

_LA = ZoneInfo("America/Los_Angeles")


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


def render_rag_stanza(rag: dict[str, Any]) -> str:
    """Render a compact RAG pipeline health line for the boot card.

    Returns an empty string when the pipeline is healthy (no actionable signals).
    Only renders when pending > 0, failures > 0, stale corpus hints > 0, or unreachable.
    """
    if not rag:
        return ""
    if rag.get("unreachable"):
        return "RAG pipeline : unreachable — skip ingest work this session\n"
    pending = rag.get("pending_contextualization", 0)
    failures = rag.get("indexing_failures", 0)
    stale = rag.get("stale_corpus_hints", 0)
    if pending == 0 and failures == 0 and stale == 0:
        return ""
    lines = ["RAG pipeline"]
    if pending:
        lines.append(
            f"  Pending contextualization : {pending} sources   (Jupiter required)"
        )
    if stale:
        lines.append(
            f"  Stale corpus hints         : {stale}          (scopes with hints newer than last classify)"
        )
    if failures:
        lines.append(f"  Indexing failures          : {failures}")
    return "\n".join(lines) + "\n"


def render_briefing_card(
    *,
    deadlines: list[dict[str, Any]] | None = None,
    unread_count: int = 0,
    unread_threads: list[dict[str, Any]] | None = None,
    review_total: int | None = None,
    review_top: list[dict[str, Any]] | None = None,
    last_session: dict[str, Any] | None = None,
    self_reflections: list[dict[str, Any]] | None = None,
    todos: list[dict[str, Any]] | None = None,
    todo_total: int = 0,
    temporal_active: list[dict[str, Any]] | None = None,
    expired_unresolved: list[dict[str, Any]] | None = None,
    transcript_continuation: dict[str, Any] | None = None,
    op_ctx_path: str = "",
    reflective_entries: list[dict[str, Any]] | None = None,
    reflective_total: int = 0,
    recent_mentions: list[dict[str, Any]] | None = None,
    recent_mentions_window_days: int = 7,
    skills: list[dict[str, Any]] | None = None,
    plan_phases: list[dict[str, Any]] | None = None,
    in_flight_todos: list[dict[str, Any]] | None = None,
    rag_state: dict[str, Any] | None = None,
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
            trigger = (s.get("description") or "").strip()
            parts.append(f"- **{slug}** — {trigger}")

    if deadlines is not None:
        parts.append("\n## Deadlines")
        if not deadlines:
            parts.append("No active deadlines.")
        else:
            for d in deadlines:
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
            parts.append(f'- **{name}**{days_tag} — "{claim_preview}"')
            parts.append(
                f"  -> If resolved, supersede: "
                f'`cortex(tool="supersede", '
                f"arguments='{{\"old_assertion_id\": {aid}, ...}}')`"
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
        parts.append(last_session.get("summary", "No summary.")[:300])
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

    if rag_state is not None:
        rag_stanza = render_rag_stanza(rag_state)
        if rag_stanza:
            parts.append(f"\n## System Health\n{rag_stanza}")

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
        parts.append(f"\n## Your Notes ({len(self_reflections)})")
        for a in self_reflections:
            session = a.get("evidence", "")
            session_tag = ""
            if session:
                m = re.search(r"(cursor|web|api|bard)-\d{4}-\d{2}-\d{2}-\d{4}", session)
                if m:
                    session_tag = f"[{m.group()}] "
            parts.append(f"- {session_tag}{a.get('claim', '')[:200]}")

    if reflective_entries:
        parts.append(f"\n## Reflective Journal ({reflective_total} total)")
        for e in reflective_entries[:5]:
            kind = e.get("kind", "entry")
            kind_tag = f" [{kind}]" if kind != "entry" else ""
            register = e.get("register", "?")
            entry_preview = (e.get("entry") or "")[:200]
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
        op_ctx_path=op_ctx_path,
        reflective_total=reflective_total,
        recent_mentions=recent_mentions,
        skills=skills,
    )
    return card, manifest
