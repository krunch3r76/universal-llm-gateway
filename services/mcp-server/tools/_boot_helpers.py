"""Boot briefing helpers — narrative rendering and response extraction."""

from __future__ import annotations

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


def build_gated_entities(
    gated_raw: list[dict[str, Any]],
    temporal_active: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build gated entity entries, tagging source as temporal/gated/both.

    Entities appearing in both temporal surfacing and the journal entity gate
    get tagged 'both' and receive enriched assertion depth in the narrative.
    """
    temporal_entity_ids: set[str] = set()
    for a in temporal_active:
        eid = a.get("entity_id")
        if eid:
            temporal_entity_ids.add(eid)

    result: list[dict[str, Any]] = []
    for entity in gated_raw:
        eid = entity.get("entity_id", "")
        source = "both" if eid in temporal_entity_ids else "gated"
        result.append({**entity, "source": source})
    return result


_MAX_NARRATIVE_TOKENS = 8000
_CHARS_PER_TOKEN = 4


def render_boot_narrative(
    *,
    boot_sections: dict[str, Any] | None = None,
    deadlines: list[dict[str, Any]] | None = None,
    temporal_active: list[dict[str, Any]] | None = None,
    temporal_upcoming: list[dict[str, Any]] | None = None,
    sessions: list[dict[str, Any]],
    suspected: list[dict[str, Any]] | None = None,
    hypothesized: list[dict[str, Any]] | None = None,
    review_total: int | None = None,
    continuation_decisions: list[dict[str, Any]] | None = None,
    continuation_services: list[dict[str, Any]] | None = None,
    todos: list[dict[str, Any]] | None = None,
    gated_entities: list[dict[str, Any]] | None = None,
    edges_summary: dict[str, int] | None = None,
    commitments: list[dict[str, Any]] | None = None,
    legal_contacts: list[dict[str, Any]] | None = None,
    activated_context: list[dict[str, Any]] | None = None,
    domain_depth_hints: list[dict[str, str]] | None = None,
    transcript_continuation: dict[str, Any] | None = None,
    recent_activity: list[dict[str, Any]] | None = None,
    capability_ref_note: str | None = None,
    max_narrative_tokens: int = _MAX_NARRATIVE_TOKENS,
) -> str:
    """Render boot briefing as Markdown narrative with token budget enforcement.

    Sections with None values are omitted entirely. This enables
    persona-scoped boot: Cursor skips deadlines, investigations,
    and review queue; Web gets everything.

    If the narrative exceeds max_narrative_tokens, sections are truncated
    from the bottom of the priority stack:
    1. Deadlines (never truncated)
    2. Open investigations
    3. Continuation state + todos
    4. Recent sessions (2-3 most recent)
    5. Session edges
    6. Temporal assertions
    7. Commitments + legal contacts
    8. Gated entities + key entity one-liners (truncated most aggressively)
    """
    import logging

    _logger = logging.getLogger(__name__)
    today = datetime.now(UTC).date()
    parts: list[str] = [f"# Boot Briefing — {today.isoformat()}"]

    if capability_ref_note:
        parts.append(f"\n{capability_ref_note}")

    if transcript_continuation:
        tc = transcript_continuation
        parts.append(f"\n## Resuming From: `{tc['entity_id']}`")
        summary = tc.get("summary", tc.get("description", ""))
        if summary:
            parts.append(f"**Session summary**: {summary}")
        parts.append(
            f"*Full transcript: `cortex(tool='entity_get', "
            f'arguments=\'{{"entity_id": "{tc["entity_id"]}"}}\')`*'
        )

    if continuation_decisions is not None or continuation_services is not None:
        parts.append("\n## Continuation State")
        has_content = False
        if continuation_decisions:
            has_content = True
            parts.append("\n**Recent decisions:**")
            for a in continuation_decisions:
                eid = a.get("entity_id", "?")
                conf = a.get("confidence", "?")
                parts.append(f"- [{eid}] ({conf}) {a.get('claim', '')}")
        if continuation_services:
            has_content = True
            parts.append("\n**Service observations:**")
            for a in continuation_services:
                eid = a.get("entity_id", "?")
                parts.append(f"- [{eid}] {a.get('claim', '')}")
        if todos:
            has_content = True
            parts.append(f"\n**Open todos** ({len(todos)}):")
            for t in todos:
                parts.append(f"- [{t.get('id', '?')}] {t.get('title', '')}")
        if not has_content:
            parts.append("No continuation state available.")

    if recent_activity:
        parts.append(f"\n## Recent Activity ({len(recent_activity)} entries)")
        for entry in recent_activity:
            agent_from = entry.get("from", "?")
            subject = entry.get("subject", "")
            created = entry.get("created_at", "")
            rel_time = _relative_time(created, datetime.now(UTC))
            parts.append(f"\n**{agent_from}** ({rel_time}) — {subject}")
            body = entry.get("body", "")
            if body:
                for line in body.strip().splitlines()[:8]:
                    parts.append(f"> {line}")
                if len(body.strip().splitlines()) > 8:
                    parts.append("> *(truncated)*")

    if edges_summary is not None:
        sup_count = edges_summary.get("supersession_chains", 0)
        reas_count = edges_summary.get("reasoning_edges", 0)
        if sup_count or reas_count:
            parts.append("\n## Session Edges (last 48h)")
            parts.append(
                f"Supersession chains: {sup_count} | Reasoning edges: {reas_count}"
            )
            parts.append(
                "*Load on demand: `cortex(tool='edges', "
                'arguments=\'{"session_id": "SESSION_ID"}\')`*'
            )

    if gated_entities:
        total_assertions = sum(e.get("assertions_shown", 0) for e in gated_entities)
        parts.append(
            f"\n## Gated Entities ({len(gated_entities)} entities, "
            f"{total_assertions} assertions surfaced)"
        )
        for entity in gated_entities:
            eid = entity.get("entity_id", "?")
            name = entity.get("entity_name", eid)
            shown = entity.get("assertions_shown", 0)
            total = entity.get("assertion_count", 0)
            source_tag = entity.get("source", "gated")
            enriched = " [enriched — temporal+gated]" if source_tag == "both" else ""
            parts.append(f"\n### {name} ({shown}/{total} assertions){enriched}")
            for a in entity.get("assertions", []):
                conf = a.get("confidence", "?")
                parts.append(f"- [{conf}] {a.get('claim', '')}")
            if total > shown:
                parts.append(f'-> entity_get("{eid}") for full context')

    if deadlines is not None:
        parts.append("\n## Deadlines")
        if not deadlines:
            parts.append("No active deadlines.")
        else:
            for d in deadlines:
                dl_date = d.get("deadline_date", "")
                remaining = ""
                if dl_date:
                    try:
                        dl = datetime.strptime(dl_date[:10], "%Y-%m-%d").date()
                        delta = (dl - today).days
                        if delta >= 0:
                            remaining = f" ({delta}d)"
                        else:
                            remaining = f" (**{abs(delta)}d OVERDUE**)"
                    except ValueError as e:
                        _logger.warning(
                            "Failed to parse deadline date '%s': %s", dl_date, e
                        )
                parts.append(
                    f"- **{dl_date}**{remaining} — "
                    f"{d.get('deadline_name', '')} ({d.get('matter_name', '')})"
                )

    if temporal_active or temporal_upcoming:
        if temporal_active:
            parts.append("\n## Temporally Active")
            for a in temporal_active:
                name = a.get("entity_name", a.get("entity_id", "?"))
                until = a.get("valid_until", "")
                remaining = ""
                if until:
                    try:
                        exp = datetime.fromisoformat(
                            until.replace("Z", "+00:00")
                        ).date()
                        delta = (exp - today).days
                        if delta == 0:
                            remaining = " (expires today)"
                        elif delta > 0:
                            remaining = f" (expires in {delta}d)"
                        else:
                            remaining = f" (**expired {abs(delta)}d ago**)"
                    except (ValueError, TypeError):
                        pass
                parts.append(f"- **{name}**{remaining} — {a.get('claim', '')}")
        if temporal_upcoming:
            parts.append("\n## Upcoming (next 7 days)")
            for a in temporal_upcoming:
                name = a.get("entity_name", a.get("entity_id", "?"))
                from_date = a.get("valid_from", "")
                starts = ""
                if from_date:
                    try:
                        start = datetime.fromisoformat(
                            from_date.replace("Z", "+00:00")
                        ).date()
                        delta = (start - today).days
                        starts = f" (in {delta}d)" if delta > 0 else " (today)"
                    except (ValueError, TypeError):
                        pass
                parts.append(f"- **{name}**{starts} — {a.get('claim', '')}")

    if boot_sections is not None:
        full = boot_sections.get("full", [])
        oneline = boot_sections.get("oneline", [])
        if full or oneline:
            parts.append("\n## Key Entities")
            for entity in full:
                parts.append(f"\n{entity.get('section_markdown', '')}")
            if oneline:
                parts.append("\n---\n\n### One-Line Summaries")
                for entity in oneline:
                    parts.append(f"- {entity.get('summary', '')}")

    if activated_context:
        activation_items = [
            a for a in activated_context if a.get("source") == "activation"
        ]
        search_items = [
            a for a in activated_context if a.get("source") == "hybrid_search"
        ]
        parts.append(f"\n## Activated Context ({len(activated_context)} assertions)")
        if activation_items:
            parts.append("\n**Via spreading activation** (structurally connected):")
            for a in activation_items:
                conf = a.get("confidence", "?")
                ent = a.get("entrenchment_score")
                score = f" (e={ent:.2f})" if ent else ""
                hop = a.get("hop_distance", "?")
                parts.append(
                    f"- [{conf}]{score} [{a.get('entity_id', '?')}, hop {hop}] {a.get('claim', '')}"
                )
        if search_items:
            parts.append("\n**Via hybrid search** (continuation-relevant):")
            for a in search_items:
                conf = a.get("confidence", "?")
                cms = a.get("combmax_score")
                score = f" (cm={cms:.2f})" if cms else ""
                parts.append(
                    f"- [{conf}]{score} [{a.get('entity_id', '?')}] {a.get('claim', '')}"
                )

    if domain_depth_hints:
        parts.append("\n## Domain Depth Available")
        parts.append("Dispatch subagent for full context on these detected domains:")
        for hint in domain_depth_hints:
            parts.append(
                f"- **{hint['domain']}**: {hint['reason']}. "
                f"Query: `{hint['dispatch_query']}`"
            )

    parts.append("\n## Recent Sessions")
    if not sessions:
        parts.append("No recent sessions.")
    else:
        for s in sessions:
            parts.append(f"\n### {s.get('timestamp', '?')} ({s.get('agent', '?')})")
            parts.append(s.get("summary", "No summary."))
            for label, field in [
                ("Decisions", "decisions"),
                ("Open items", "open_items"),
            ]:
                val = s.get(field)
                if val:
                    items = list(val)
                    fenced = [f"> {str(i)}" for i in items]
                    parts.append(f"**{label}**:\n" + "\n".join(fenced))

    if commitments is not None:
        if commitments:
            parts.append(f"\n## Open Commitments ({len(commitments)})")
            for c in commitments:
                name = c.get("entity_name", c.get("entity_id", "?"))
                vf = c.get("valid_from", "")
                since = f" (since {vf})" if vf else ""
                parts.append(f"- **{name}**{since} — {c.get('claim', '')}")
        else:
            parts.append("\n## Open Commitments\nNo pending commitments.")

    if legal_contacts:
        parts.append(f"\n## Legal Matter Contacts ({len(legal_contacts)})")
        for contact in legal_contacts:
            name = contact.get("entity_name", contact.get("entity_id", "?"))
            etype = contact.get("entity_type", "?")
            parts.append(f"\n### {name} ({etype})")
            for a in contact.get("assertions", []):
                conf = a.get("confidence", "?")
                parts.append(f"- [{conf}] {a.get('claim', '')}")

    if suspected is not None or hypothesized is not None:
        parts.append("\n## Open Investigations")
        s_list = suspected or []
        h_list = hypothesized or []
        if not s_list and not h_list:
            parts.append("No open investigations.")
        else:
            for label, items in [("Suspected", s_list), ("Hypothesized", h_list)]:
                if items:
                    parts.append(f"\n**{label}** ({len(items)}):")
                    for a in items:
                        parts.append(
                            f"- [{a.get('entity_id', '?')}] {a.get('claim', '')}"
                        )

    if review_total is not None:
        parts.append(f"\n## Review Queue\n{review_total} item(s) pending review.")

    narrative = "\n".join(parts)
    max_chars = max_narrative_tokens * _CHARS_PER_TOKEN
    if len(narrative) <= max_chars:
        return narrative

    _logger.warning(
        "boot_narrative %d chars exceeds budget %d (%d tokens). Truncating low-priority sections.",
        len(narrative),
        max_chars,
        max_narrative_tokens,
    )
    # Priority order (bottom truncates first):
    # Never truncated: Deadlines, Continuation State, Agent Bus
    # Truncated last→first: Key Entities, Commitments, Legal Contacts,
    # Temporal, Session Edges, Recent Sessions, Gated Entities
    truncation_targets = [
        "## Gated Entities",
        "## One-Line Summaries",
        "## Legal Matter Contacts",
        "## Open Commitments",
        "## Session Edges",
        "## Recent Sessions",
        "## Upcoming",
        "## Temporally Active",
        "## Key Entities",
    ]
    for target in truncation_targets:
        if len(narrative) <= max_chars:
            break
        idx = narrative.find(f"\n{target}")
        if idx < 0:
            idx = narrative.find(f"\n### {target.lstrip('# ')}")
        if idx > 0:
            next_section = narrative.find("\n## ", idx + len(target) + 2)
            if next_section > 0:
                narrative = narrative[:idx] + narrative[next_section:]
            else:
                narrative = narrative[:idx]

    if len(narrative) > max_chars:
        narrative = narrative[:max_chars]

    sections_removed = sum(1 for t in truncation_targets if f"\n{t}" not in narrative)
    if sections_removed:
        narrative += f"\n\n*[{sections_removed} section(s) truncated for token budget]*"

    return narrative
