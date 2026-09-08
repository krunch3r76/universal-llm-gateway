"""Render transcript projection state to markdown with budget collapse.

Pure function of state JSON plus preserved ``## Operator notes`` from the
prior render. Re-renders from scratch each run; only operator notes survive.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

_OPERATOR_NOTES_RE = re.compile(
    r"(?ms)^## Operator notes\s*\n(.*?)(?:\n## |\Z)",
)


def extract_operator_notes(md_text: str) -> str:
    """Return preserved operator notes body from an existing render."""
    match = _OPERATOR_NOTES_RE.search(md_text or "")
    return match.group(1).rstrip("\n") if match else ""


def _window_activity_key(window: dict[str, Any]) -> str:
    jsonl = window.get("jsonl") or {}
    return str(jsonl.get("mtime") or window.get("first_seen") or "")


def cp_cells_by_ordinal(state: dict[str, Any]) -> list[dict[str, Any]]:
    """CHECKPOINT cells across windows, sorted by ``cp_ordinal`` (AC-15-2)."""
    items: list[dict[str, Any]] = []
    for tid, window in (state.get("windows") or {}).items():
        for cell in window.get("cells") or []:
            boundary = cell.get("boundary") or {}
            if boundary.get("kind") != "CHECKPOINT":
                continue
            items.append(
                {
                    "transcript_id": tid,
                    "cell": cell,
                    "cp_ordinal": boundary.get("cp_ordinal"),
                    "turn_number": boundary.get("turn_number"),
                }
            )
    items.sort(
        key=lambda row: (
            row["cp_ordinal"] is None,
            row["cp_ordinal"] if row["cp_ordinal"] is not None else 0,
        )
    )
    return items


def derive_open_interval(state: dict[str, Any]) -> dict[str, Any]:
    """Unsealed tail turns per window — matches ``open_line.open_interval`` (AC-15-3)."""
    transcript_ids: list[str] = []
    total_turns = 0
    windows: list[dict[str, Any]] = []
    for tid, window in (state.get("windows") or {}).items():
        tail = window.get("open_tail")
        if not tail:
            continue
        lo = int(tail.get("turn_lo", 0))
        hi = int(tail.get("turn_hi", 0))
        if hi <= lo:
            continue
        turns = hi - lo
        transcript_ids.append(tid)
        total_turns += turns
        windows.append(
            {
                "transcript_id": tid,
                "turn_lo": lo,
                "turn_hi": hi,
                "turns": turns,
            }
        )
    return {
        "transcript_ids": transcript_ids,
        "turns": total_turns,
        "windows": windows,
    }


def render_projection_markdown(
    state: dict[str, Any],
    *,
    state_sha256: str,
    render_budget_bytes: int = 98_304,
    prior_md: str = "",
) -> tuple[str, int]:
    """Render state to markdown; return (text, collapsed_window_count)."""
    thread = state.get("thread", "")
    windows: dict[str, Any] = state.get("windows", {})
    operator_notes = extract_operator_notes(prior_md)
    ordered_ids = sorted(
        windows.keys(),
        key=lambda tid: _window_activity_key(windows[tid]),
        reverse=True,
    )
    lines: list[str] = [
        "<!-- transcript-projection v1 · derived — re-rendered every run; "
        'only "## Operator notes" is preserved -->',
        f"# {thread} — transcript projection",
        (
            f"state: cortex://notes/system/threads/{thread}-transcript-projection.state.json "
            f"· sha256:{state_sha256} · updated {state.get('updated_at', '')} "
            f"· runs {state.get('runs', 0)} · windows {len(windows)} "
            f"· cells {sum(len(w.get('cells', [])) for w in windows.values())}"
        ),
        "",
        "## Open line",
        _open_line(state),
        "",
        "## Registry (derived — supersedes the manual table)",
        "| transcript_id | tab | lane | binding | turns | cells | last boundary | note | source |",
        "|---|---|---|---|---:|---:|---|---|---|",
    ]
    collapsed = 0
    registry_rows = [_registry_row(tid, windows[tid]) for tid in ordered_ids]
    for row in registry_rows:
        lines.append(row)
    lines.extend(["", "## Cells (cp_ordinal order)"])
    cp_cells = cp_cells_by_ordinal(state)
    if cp_cells:
        lines.extend(
            [
                "| cp | bus_turn | window | turns | note |",
                "|---|---:|---|---|---|",
            ]
        )
        for row in cp_cells:
            cell = row["cell"]
            boundary = cell.get("boundary") or {}
            tid = row["transcript_id"]
            lines.append(
                f"| {row.get('cp_ordinal', '')} | {row.get('turn_number', '')} "
                f"| `{tid[:8]}…` | ({cell.get('turn_lo')},{cell.get('turn_hi')}] "
                f"| {cell.get('note', '')[:80]} |"
            )
    else:
        lines.append("_none_")
    open_interval = derive_open_interval(state)
    lines.extend(["", "## Open interval (derived)"])
    if open_interval["windows"]:
        lines.append(
            f"transcript_ids: {len(open_interval['transcript_ids'])} · "
            f"turns: {open_interval['turns']}"
        )
        for row in open_interval["windows"]:
            lines.append(
                f"- `{row['transcript_id'][:8]}…` · turns "
                f"{row['turn_lo']}→{row['turn_hi']} unsealed "
                f"({row['turns']} turns)"
            )
    else:
        lines.append("_none_")
    lines.extend(
        [
            "",
            "## Recent cells (last 8 by projected_at)",
            "| window | cell | boundary | note |",
            "|---|---|---|---|",
        ]
    )
    recent = _recent_cells(windows, limit=8)
    for row in recent:
        lines.append(row)
    lines.extend(
        [
            "",
            "## Mismatch (derived)",
        ]
    )
    mismatches = state.get("anchor_mismatches", [])
    if mismatches:
        lines.extend(
            [
                "| cp_turn | cp_ordinal | claimed | observed | kind |",
                "|---|---:|---|---|---|",
            ]
        )
        for row in mismatches:
            lines.append(
                f"| {row.get('cp_turn')} | {row.get('cp_ordinal', '')} | "
                f"{row.get('claimed')} | {row.get('observed')} | {row.get('kind')} |"
            )
    else:
        lines.append("none")
    detail_ids = list(ordered_ids)
    collapsed = 0
    while detail_ids:
        body = _render_with_detail(lines, windows, detail_ids, operator_notes, thread)
        if len(body.encode("utf-8")) <= render_budget_bytes:
            return body, collapsed
        collapsed += 1
        detail_ids.pop()
    body = _render_with_detail(lines, windows, [], operator_notes, thread)
    return body, collapsed


def _open_line(state: dict[str, Any]) -> str:
    mismatches = len(state.get("anchor_mismatches", []))
    last_run = state.get("last_run") or {}
    latest = _latest_window(state)
    parts = [
        f"thread={state.get('thread')}",
        f"windows={len(state.get('windows', {}))}",
        f"anchor_mismatches={mismatches}",
        f"parsed={last_run.get('parsed', 0)}",
    ]
    if latest:
        parts.append(
            f"latest={latest['transcript_id'][:8]} turns={latest.get('turn_count')} "
            f"note={latest.get('last_note', '')[:80]}"
        )
    return " · ".join(parts)


def _latest_window(state: dict[str, Any]) -> dict[str, Any] | None:
    windows = state.get("windows") or {}
    if not windows:
        return None
    tid = max(windows, key=lambda k: _window_activity_key(windows[k]))
    w = windows[tid]
    note = ""
    cells = w.get("cells") or []
    if cells:
        note = cells[-1].get("note", "")
    elif w.get("open_tail"):
        note = w["open_tail"].get("note", "")
    return {
        "transcript_id": tid,
        "tab_title": w.get("tab_title", ""),
        "lane": w.get("lane", ""),
        "turn_count": w.get("turn_count", 0),
        "closed_hi": w.get("closed_hi", 0),
        "last_note": note,
    }


def _registry_row(tid: str, window: dict[str, Any]) -> str:
    cells = window.get("cells") or []
    last_boundary = ""
    note = ""
    if cells:
        boundary = cells[-1].get("boundary") or {}
        last_boundary = str(boundary.get("subject_head") or boundary.get("kind") or "")
        note = cells[-1].get("note", "")
    return (
        f"| `{tid[:8]}…` | {window.get('tab_title', '')} | {window.get('lane', '')} "
        f"| {window.get('binding', '')} | {window.get('turn_count', 0)} "
        f"| {len(cells)} | {last_boundary[:40]} | {note[:60]} | {window.get('source', '')} |"
    )


def _recent_cells(windows: dict[str, Any], *, limit: int) -> list[str]:
    items: list[tuple[str, dict[str, Any]]] = []
    for tid, window in windows.items():
        for cell in window.get("cells", []):
            items.append((tid, cell))
        if window.get("open_tail"):
            items.append((tid, window["open_tail"]))
    items.sort(key=lambda pair: pair[1].get("projected_at", ""), reverse=True)
    rows: list[str] = []
    for tid, cell in items[:limit]:
        boundary = cell.get("boundary") or {}
        rows.append(
            f"| `{tid[:8]}…` | ({cell.get('turn_lo')},{cell.get('turn_hi')}] "
            f"| {boundary.get('kind', '')} | {cell.get('note', '')[:80]} |"
        )
    return rows


def _render_with_detail(
    prefix_lines: list[str],
    windows: dict[str, Any],
    detail_ids: list[str],
    operator_notes: str,
    thread: str,
) -> str:
    lines = list(prefix_lines)
    lines.extend(["", "## Windows"])
    for tid in detail_ids:
        window = windows[tid]
        lines.append(
            f"### {window.get('tab_title', tid[:8])} — `{tid[:8]}` · "
            f"lane {window.get('lane')} · {window.get('binding')} · "
            f"turns 1..{window.get('turn_count')} · {window.get('source')}"
        )
        lines.append("| cell | boundary | note | highlight | bus w/r | artifacts | dispatches |")
        lines.append("|---|---|---|---|---|---|---|")
        for cell in window.get("cells", []):
            facts = cell.get("facts") or {}
            touch = facts.get("bus_touch") or {}
            wsum = sum(t.get("w", 0) for t in touch.values())
            rsum = sum(t.get("r", 0) for t in touch.values())
            boundary = cell.get("boundary") or {}
            highlight = str(cell.get("highlight") or "")
            lines.append(
                f"| ({cell.get('turn_lo')},{cell.get('turn_hi')}] "
                f"| {boundary.get('kind', '')} | {cell.get('note', '')[:60]} "
                f"| {highlight} "
                f"| {wsum}/{rsum} | {len(facts.get('artifacts', []))} "
                f"| {len(facts.get('dispatches', []))} |"
            )
        if window.get("open_tail"):
            cell = window["open_tail"]
            lines.append(
                f"| ({cell.get('turn_lo')},{cell.get('turn_hi')}] | open_tail "
                f"| {cell.get('note', '')[:60]} | | | | |"
            )
        lines.append("")
    child_lines = [tid for tid in detail_ids if windows[tid].get("lane") != thread]
    if child_lines:
        lines.extend(["## Child-lane windows", ""])
        for tid in child_lines:
            lines.append(_registry_row(tid, windows[tid]))
    lines.extend(["", "## Operator notes", operator_notes, ""])
    return "\n".join(lines)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


__all__ = [
    "cp_cells_by_ordinal",
    "derive_open_interval",
    "extract_operator_notes",
    "render_projection_markdown",
    "sha256_text",
]
