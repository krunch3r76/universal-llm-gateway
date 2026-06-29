"""Briefing-card Agent Rules section — seat-filtered, trigger-ranked rule INDEX.

Rule rows arrive from GET /skills?view=boot&layer=rules with the SAME envelope as
skill rows (both projected by routes/_skill_index.py::boot_skill_row), so the
shared ranking primitives apply unchanged. Manifest-only: bodies are NEVER
inlined; each line carries the source_uri + digest needed to pull the body via
GET /skills/body?id=<entity_id>&expected_digest=<digest>.
"""

from __future__ import annotations

from typing import Any

from ._skill_bodies import skill_slug

_RULES_BYTE_BUDGET = 4096
_RULES_INLINE_MAX = 12
_FOL_OPERATORS = {"∨", "∧", "⇒", "⇔", "¬", "→", "∈", "∉", "∪", "∩", "⊆", "⊂", "|"}


def _normalize_terms(row: dict[str, Any]) -> set[str]:
    terms = row.get("trigger_match_terms") or []
    if terms:
        return {t.lower() for t in terms}
    raw = row.get("trigger_short") or row.get("description_first_sentence") or ""
    for op in _FOL_OPERATORS:
        raw = raw.replace(op, " ")
    return {tok for tok in raw.lower().split() if len(tok) > 2}


def _rank_score(row: dict[str, Any], signals: set[str]) -> int:
    return len(_normalize_terms(row) & signals)


def _append_rule_index(
    lines: list[str], bucket: list[dict[str, Any]], *, names_only: bool = False
) -> None:
    """Emit `- **slug** — trigger (source_uri · digest)` per rule.

    Unlike the skills index (which surfaces source_uri for the invariant tier
    only), the rule index surfaces source_uri + digest for EVERY row so any
    rule body is pull-on-demand. names_only collapses to slug under byte
    pressure while keeping every slug discoverable.
    """
    for rule in sorted(bucket, key=skill_slug):
        slug = skill_slug(rule)
        if names_only:
            lines.append(f"- **`{slug}`**")
            continue
        trigger = (
            rule.get("trigger_short") or rule.get("description_first_sentence") or ""
        ).strip()
        trigger_part = f" — {trigger}" if trigger else ""
        lines.append(f"- **`{slug}`**{trigger_part}")


def render_rules_section(
    rules: list[dict[str, Any]], boot_signals: set[str] | None = None
) -> list[str]:
    """Render a bounded, trigger-ranked Agent Rules INDEX (manifest-only).

    Two tiers: session-relevant (trigger-matched, ranked by current boot
    signals) then catalog (remaining, by category). Bodies are NOT inlined.
    Returns [] when there are no seat-applicable rules (section omitted).
    """
    if not rules:
        return []
    signals = boot_signals or set()
    lines: list[str] = [
        f"\n## Agent Rules ({len(rules)} on this seat — manifest only)",
        (
            "> Seat-filtered conduct/rule layer. Bodies pull-on-demand via "
            "`GET /skills/body?id=<entity_id>` or fs read of source_uri. "
            "Ranked by current session signals."
        ),
    ]
    ranked = sorted(
        ((_rank_score(r, signals), r) for r in rules),
        key=lambda p: (-p[0], skill_slug(p[1])),
    )
    relevant = [r for score, r in ranked if score > 0][:_RULES_INLINE_MAX]
    relevant_ids = {id(r) for r in relevant}
    catalog = [r for r in rules if id(r) not in relevant_ids]

    if relevant:
        lines.append("\n### Relevant now")
        _append_rule_index(lines, relevant)

    if catalog:
        lines.append("\n### Catalog")
        names_only = sum(len(s.encode("utf-8")) for s in lines) >= _RULES_BYTE_BUDGET
        by_cat: dict[str | None, list[dict[str, Any]]] = {}
        for r in catalog:
            by_cat.setdefault(r.get("skill_category"), []).append(r)
        for cat in sorted(k for k in by_cat if k is not None):
            lines.append(f"**{cat}** ({len(by_cat[cat])})")
            _append_rule_index(lines, by_cat[cat], names_only=names_only)
            if (
                not names_only
                and sum(len(x.encode("utf-8")) for x in lines) >= _RULES_BYTE_BUDGET
            ):
                names_only = True
        uncategorized = by_cat.get(None, [])
        if uncategorized:
            lines.append("**uncategorized**")
            _append_rule_index(lines, uncategorized, names_only=names_only)
    return lines
