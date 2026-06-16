"""Shared render-only concise skill index formatter (F2b-i).

Pure function — no SQL, seat filter, or ranking. Consumes already-projected
manifest or boot item dicts from GET /skills.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

_LOAD_CONTRACT_LINE = (
    "> **Load a body**: `GET /skills/body?id=agent_skill:<slug>&expected_digest=<digest>` "
    "(REST) or `fs(cortex, md_read, agent-skills/<slug>.md)`."
)


def _row_slug(row: Mapping[str, Any]) -> str:
    entity_id = row.get("id") or row.get("entity_id")
    if isinstance(entity_id, str) and entity_id.strip():
        slug = entity_id.strip().removeprefix("agent_skill:")
        if slug:
            return slug
    name = row.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return "?"


def _prose_display(row: Mapping[str, Any]) -> str:
    return (
        row.get("description_first_sentence")
        or row.get("trigger_short")
        or row.get("trigger")
        or ""
    ).strip()


def _trigger_display(row: Mapping[str, Any]) -> str:
    return (row.get("trigger_short") or row.get("trigger") or "").strip()


def render_concise_skill_index(
    rows: Sequence[Mapping[str, Any]],
    *,
    title: str | None = None,
    include_category: bool = True,
    include_digest: bool = False,
    include_source_hint: bool = True,
    max_trigger_chars: int = 160,
) -> str:
    """Render a concise markdown skill index from projected item rows."""
    lines: list[str] = []
    if title:
        lines.append(title)
        lines.append("")
    lines.append(
        f"Seat-filtered manifest ({len(rows)} skills). Bodies load on demand."
    )
    if include_source_hint:
        lines.append("")
        lines.append(_LOAD_CONTRACT_LINE)
    lines.append("")

    if include_category:
        by_cat: dict[str, list[Mapping[str, Any]]] = {}
        for row in rows:
            cat = str(row.get("skill_category") or "uncategorized")
            by_cat.setdefault(cat, []).append(row)
        for cat in sorted(by_cat):
            lines.append(f"## {cat}")
            lines.append("")
            for row in sorted(by_cat[cat], key=_row_slug):
                _append_row_lines(
                    lines,
                    row,
                    include_digest=include_digest,
                    max_trigger_chars=max_trigger_chars,
                )
    else:
        for row in sorted(rows, key=_row_slug):
            _append_row_lines(
                lines,
                row,
                include_digest=include_digest,
                max_trigger_chars=max_trigger_chars,
            )

    return "\n".join(lines).rstrip() + "\n"


def _append_row_lines(
    lines: list[str],
    row: Mapping[str, Any],
    *,
    include_digest: bool,
    max_trigger_chars: int,
) -> None:
    slug = _row_slug(row)
    name = str(row.get("name") or slug)
    prose = _prose_display(row)
    trigger = _trigger_display(row)
    if max_trigger_chars and len(trigger) > max_trigger_chars:
        trigger = trigger[: max_trigger_chars - 1] + "…"
    lines.append(f"### {slug}")
    if name != slug:
        lines.append(f"- **Name**: {name}")
    if prose:
        lines.append(f"- **Summary**: {prose}")
    if trigger and trigger != prose:
        lines.append(f"- **Trigger**: {trigger}")
    if include_digest:
        digest = row.get("digest")
        if isinstance(digest, str) and digest.strip():
            lines.append(f"- **Digest**: {digest.strip()}")
    lines.append("")


__all__ = ["render_concise_skill_index"]
