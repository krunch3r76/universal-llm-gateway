"""Boot briefing helpers — narrative rendering, briefing card, and response extraction."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from ._operational_context import (
    AGENT_PERSONA_SEEDS as AGENT_PERSONA_SEEDS,  # noqa: PLC0414
)
from ._operational_context import (
    render_operational_context as render_operational_context,  # noqa: PLC0414
)


def _relative_time(iso_str: str | None, now: datetime) -> str:
    """Format an ISO timestamp as a human-readable relative time."""
    if not iso_str:
        return "unknown"
    try:
        ts = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        delta_s = (now - ts).total_seconds()
        if delta_s < 0:
            return "just now"
        if delta_s < 3600:
            return f"{int(delta_s / 60)}m ago"
        if delta_s < 86400:
            return f"{int(delta_s / 3600)}h ago"
        return f"{int(delta_s / 86400)}d ago"
    except (ValueError, TypeError):
        return "unknown"


def safe_list(raw: dict[str, Any] | list[Any], key: str = "items") -> list[Any]:
    """Extract a list from an API response, returning [] on error."""
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        if "error" in raw:
            return []
        return raw.get(key, [])
    return []


def _resolved_key_phrases(recently_resolved: list[dict[str, Any]]) -> set[str]:
    """Extract specific key phrases from recently-resolved temporal claims.

    Uses the first 4 words of each claim as a matching key. Only phrases that
    contain a distinguishing token — a digit/dollar amount, or a word with 8+
    characters — are included. Phrases starting with generic sentence openers
    (pronouns, helper verbs) are excluded to avoid false-positive matches
    against unrelated open_items describing similar-but-distinct events.
    """
    generic_starters = frozenset(
        {
            "has",
            "have",
            "had",
            "is",
            "are",
            "was",
            "were",
            "be",
            "been",
            "will",
            "would",
            "could",
            "should",
            "may",
            "might",
            "he",
            "she",
            "it",
            "they",
            "we",
            "kaywan",
            "there",
            "this",
            "that",
        }
    )
    common_short = frozenset(
        {
            "the",
            "a",
            "an",
            "and",
            "or",
            "not",
            "on",
            "in",
            "at",
            "to",
            "of",
            "for",
            "from",
            "by",
            "as",
            "with",
            "its",
        }
    )

    phrases: set[str] = set()
    for r in recently_resolved:
        claim = r.get("claim") or ""
        words = claim.split()[:4]
        if len(words) < 3:
            continue
        # Skip phrases that start with generic/pronoun words — they describe
        # ongoing behavioral patterns, not specific trackable facts.
        if words[0].lower().rstrip(".,;:") in generic_starters:
            continue
        phrase = " ".join(words).lower()
        # Require at least one distinguishing token: a digit/$ amount, or a word
        # 8+ chars that is not a common stop word.
        has_distinguishing = any(
            any(ch.isdigit() or ch == "$" for ch in w)
            or (len(w) >= 8 and w.lower().rstrip(".,;:") not in common_short)
            for w in words
        )
        if has_distinguishing:
            phrases.add(phrase)
    return phrases


def filter_stale_open_items(
    sessions: list[dict[str, Any]],
    recently_resolved: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Tag open_items in sessions that reference recently-resolved temporal matters.

    When a temporal assertion (valid_until set) is superseded — meaning the matter
    was resolved — session journals that still carry it as an open_item receive a
    '[RESOLVED]' prefix. This prevents the item from appearing as actionable in
    future boot briefings while preserving a visible audit trail.
    """
    if not recently_resolved:
        return sessions

    key_phrases = _resolved_key_phrases(recently_resolved)
    if not key_phrases:
        return sessions

    result: list[dict[str, Any]] = []
    for session in sessions:
        open_items = session.get("open_items") or []
        tagged: list[str] = []
        for item in open_items:
            item_lower = str(item).lower()
            if any(phrase in item_lower for phrase in key_phrases):
                tagged.append(f"[RESOLVED] {item}")
            else:
                tagged.append(item)
        result.append({**session, "open_items": tagged})
    return result


# ── Slim briefing card ──────────────────────────────────────────────────────


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
    self_reflections: list[dict[str, Any]] | None = None,
    todos: list[dict[str, Any]] | None = None,
    todo_total: int = 0,
    temporal_active: list[dict[str, Any]] | None = None,
    expired_unresolved: list[dict[str, Any]] | None = None,
    transcript_continuation: dict[str, Any] | None = None,
    op_ctx_path: str = "",
) -> tuple[str, list[dict[str, Any]]]:
    """Render a compact briefing card (~3-5KB) and section manifest.

    Returns (card_markdown, sections_available).
    The card contains only priority signals — enough for the agent to orient
    and decide what to pull deeper. Heavy sections are replaced with counts
    and fetch hints in the manifest.
    """
    now = datetime.now(UTC)
    today = now.date()
    parts: list[str] = [f"# Boot Briefing — {today.isoformat()}"]

    if transcript_continuation:
        tc = transcript_continuation
        parts.append(f"\n## Resuming From: `{tc.get('entity_id', '?')}`")
        summary = tc.get("summary", tc.get("description", ""))
        if summary:
            parts.append(f"**Summary**: {summary}")

    # ── Deadlines (never truncated) ──
    if deadlines is not None:
        parts.append("\n## Deadlines")
        if not deadlines:
            parts.append("No active deadlines.")
        else:
            for d in deadlines:
                parts.append(_deadline_line(d, now))

    # ── Expired unresolved (action needed) ──
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

    # ── Temporal (active only, compact) ──
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

    # ── Agent bus ──
    if unread_count > 0:
        thread_slugs = ", ".join(
            t.get("slug", t.get("id", "?")) for t in (unread_threads or [])
        )
        parts.append(f"\n## Agent Bus — {unread_count} unread")
        if thread_slugs:
            parts.append(f"Threads with unread: {thread_slugs}")

    # ── Review queue ──
    if review_total is not None and review_total > 0:
        parts.append(f"\n## Review Queue — {review_total} item(s)")
        for item in (review_top or [])[:3]:
            reason = item.get("reason", "")
            name = item.get("name", item.get("id", "?"))
            parts.append(f"- [{reason}] {name}")

    # ── Last session ──
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

    # ── Todos (top 5) ──
    if todos:
        parts.append(f"\n## Todos — {todo_total} open")
        for t in todos[:5]:
            priority = t.get("priority", "")
            p_tag = f" [{priority}]" if priority else ""
            parts.append(f"- `{t.get('id', '?')}`{p_tag} {t.get('title', '')}")
        if todo_total > 5:
            parts.append(
                f"- *…{todo_total - 5} more — "
                "`cortex(tool='entities', arguments='{\"type\": \"todo\"}')`*"
            )

    # ── Self-observations ──
    if self_reflections:
        parts.append(f"\n## Your Notes ({len(self_reflections)})")
        for a in self_reflections:
            session = a.get("evidence", "")
            session_tag = ""
            if session:
                m = re.search(r"(cursor|web|api)-\d{4}-\d{2}-\d{2}-\d{4}", session)
                if m:
                    session_tag = f"[{m.group()}] "
            parts.append(f"- {session_tag}{a.get('claim', '')[:200]}")

    card = "\n".join(parts)

    # ── Section manifest ──
    manifest: list[dict[str, Any]] = []
    if todo_total > 0:
        manifest.append(
            {
                "section": "todos",
                "count": todo_total,
                "hint": "cortex(tool='entities', arguments='{\"type\": \"todo\"}')",
            }
        )
    manifest.append(
        {
            "section": "sessions",
            "hint": "cortex(tool='journal_read', arguments='{\"limit\": 5}')",
        }
    )
    if unread_count > 0:
        manifest.append(
            {
                "section": "bus",
                "unread": unread_count,
                "hint": 'agent_bus(tool=\'fetch\', arguments=\'{"thread": "480", "last": 10}\')',
            }
        )
    if op_ctx_path:
        manifest.append(
            {
                "section": "operational_context",
                "hint": f"fs(sandbox='files', op='md_list', path='{op_ctx_path}')",
            }
        )
    manifest.append(
        {
            "section": "self_reflections",
            "hint": "cortex(tool='assertions', arguments='{\"entity_id\": \"ai_agent:AGENT\"}')",
        }
    )
    manifest.append(
        {
            "section": "deadlines",
            "hint": "cortex(tool='deadlines')",
        }
    )

    return card, manifest
