"""Build budgeted bus pointer bodies with markdown section maps."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Literal

from markdown_sections import list_sections as md_list_sections

BodyMode = Literal["compact", "inline", "pointer_only"]

MAX_BODY_CHARS = 2000
TARGET_BODY_CHARS = 1500
MAX_SECTION_ROWS = 12

_VERDICT_LINE_RE = re.compile(r"(?m)^\s*(VERDICT:[^\n]*)$")


def extract_verdict_line(text: str) -> str | None:
    """First unfenced VERDICT line, if any."""
    if not text:
        return None
    from markdown_fence import MIN_FENCE_LEN, is_closing_fence_line, parse_fence_open

    in_fence = False
    fence_char = ""
    fence_len = 0
    for line in text.splitlines():
        stripped = line.strip()
        if not in_fence:
            parsed = parse_fence_open(stripped)
            if parsed is not None:
                ch, ln, _lang = parsed
                if ln >= MIN_FENCE_LEN:
                    in_fence = True
                    fence_char, fence_len = ch, ln
                    continue
        elif is_closing_fence_line(stripped, fence_char, fence_len):
            in_fence = False
            fence_char, fence_len = "", 0
            continue
        if in_fence:
            continue
        match = _VERDICT_LINE_RE.match(line)
        if match:
            return match.group(1).strip()
    return None


def _char_offset_for_line(text: str, line_one_indexed: int) -> int:
    if line_one_indexed <= 1:
        return 0
    lines = text.splitlines(keepends=True)
    idx = min(line_one_indexed - 1, len(lines))
    return sum(len(lines[i]) for i in range(idx))


def build_section_map(
    text: str,
    *,
    max_rows: int = MAX_SECTION_ROWS,
) -> tuple[list[dict[str, Any]], int]:
    """Heading rows with char offsets; returns (rows, hidden_count)."""
    if not text.strip():
        return [], 0
    raw = md_list_sections(text)
    rows: list[dict[str, Any]] = []
    for row in raw:
        line = int(row.get("line", 0))
        rows.append(
            {
                "heading": str(row.get("heading", "")),
                "level": int(row.get("level", 0)),
                "offset": _char_offset_for_line(text, line),
                "line": line,
            }
        )
    hidden = max(0, len(rows) - max_rows)
    if hidden:
        rows = rows[:max_rows]
    return rows, hidden


def _format_section_table(
    rows: list[dict[str, Any]],
    *,
    tail: str | None,
) -> list[str]:
    lines = ["## sections", "| heading | level | offset | line |", "|---|---:|---:|---:|"]
    for row in rows:
        heading = str(row["heading"]).replace("|", "\\|")[:80]
        lines.append(
            f"| {heading} | {row['level']} | {row['offset']} | {row['line']} |"
        )
    if tail:
        lines.append(f"| {tail} | | | |")
    return lines


def _recompute_section_tail(
    *,
    sidecar_uri: str,
    original_row_count: int,
    visible_rows: int,
) -> str | None:
    hidden = max(0, original_row_count - visible_rows)
    if hidden <= 0:
        return None
    rel = sidecar_uri.removeprefix("cortex://")
    return f"+{hidden} more (md_list {rel})"


def compact_pointer_body(
    *,
    verdict: str,
    execution_id: str | None = None,
    dispatch_id: str | None = None,
    satellite_execution_id: str | None = None,
    archive_uri: str | None = None,
    content_proof_uri: str | None = None,
    protected_lines: list[str] | None = None,
    sidecar_uri: str,
    sidecar_sha256: str,
    section_map: list[dict[str, Any]] | None = None,
    context_lines: list[str] | None = None,
    section_tail: str | None = None,
    section_map_source_rows: int | None = None,
    max_chars: int = MAX_BODY_CHARS,
) -> str:
    """YAML-ish compact bus body; truncates section rows then context first."""
    verdict_line = verdict if verdict.startswith("VERDICT:") else f"VERDICT: {verdict}"
    header: list[str] = [
        verdict_line,
        "",
        f"sidecar_ref: `{sidecar_uri}`",
        f"sha256: `{sidecar_sha256}`",
    ]
    if execution_id:
        header.append(f"execution_id: `{execution_id}`")
    if satellite_execution_id:
        header.append(f"satellite_execution_id: `{satellite_execution_id}`")
    if dispatch_id:
        header.append(f"dispatch_id: `{dispatch_id}`")
    if archive_uri:
        header.append(f"archive_uri: `{archive_uri}`")
    if content_proof_uri:
        header.append(f"content_proof_uri: `{content_proof_uri}`")
    for line in protected_lines or []:
        if line and line not in header:
            header.append(line)

    sections = list(section_map or [])
    original_section_rows = (
        section_map_source_rows if section_map_source_rows is not None else len(sections)
    )
    contexts = [ln.strip() for ln in (context_lines or []) if ln.strip()]
    tail = section_tail

    def _render() -> str:
        nonlocal tail
        visible = len(sections)
        tail = _recompute_section_tail(
            sidecar_uri=sidecar_uri,
            original_row_count=original_section_rows,
            visible_rows=visible,
        )
        parts = list(header)
        if sections or tail:
            parts.append("")
            parts.extend(
                _format_section_table(sections, tail=tail),
            )
        if contexts:
            parts.append("")
            parts.append("## context")
            parts.extend(f"- {ln}" for ln in contexts)
        return "\n".join(parts)

    def _shrink_verdict_header() -> None:
        mandatory = "\n".join(header[1:])
        budget = max_chars - len(mandatory) - 1
        if budget < 1:
            budget = 1
        verdict_line = header[0]
        if len(verdict_line) > budget:
            ellipsis = "…"
            if verdict_line.startswith("VERDICT:") and budget > len(ellipsis):
                header[0] = verdict_line[: budget - len(ellipsis)] + ellipsis
            else:
                header[0] = verdict_line[:budget]

    out = _render()
    while len(out) > max_chars:
        if sections:
            sections.pop()
            out = _render()
            continue
        if contexts:
            contexts.pop()
            out = _render()
            continue
        break
    while out and len(out) > max_chars and contexts:
        contexts[-1] = contexts[-1][: max(16, len(contexts[-1]) // 2)].rstrip()
        if not contexts[-1]:
            contexts.pop()
        out = _render()
    if len(out) > max_chars:
        _shrink_verdict_header()
        out = "\n".join(header)
    return out


@dataclass(frozen=True, slots=True)
class CloseoutMemoBusDelivery:
    """Formatted bus body plus optional spill fields for POST /turns."""

    body: str
    sidecar_content: str | None = None
    allow_long_body: bool = False


def giw_parent_pointer_enrichment(
    *,
    sidecar_uri: str,
    sidecar_text: str | None,
    base_body: str,
    max_chars: int = MAX_BODY_CHARS,
) -> str:
    """Append sha256 + section map (or unavailable line) within ``max_chars``."""
    if sidecar_text is None:
        note = f"sections: unavailable (md_list {sidecar_uri.strip()})"
        body = f"{base_body}\n{note}"
        return body if len(body) <= max_chars else base_body

    digest = hashlib.sha256(sidecar_text.encode("utf-8")).hexdigest()
    section_map, hidden = build_section_map(sidecar_text)
    original_rows = len(section_map) + hidden
    tail: str | None = None
    if hidden:
        rel = sidecar_uri.removeprefix("cortex://")
        tail = f"+{hidden} more (md_list {rel})"

    sections = list(section_map)
    sha_line = f"sha256: {digest}"

    def _render() -> str:
        lines = [base_body, sha_line]
        if sections or tail:
            lines.append("")
            lines.extend(_format_section_table(sections, tail=tail))
        return "\n".join(lines)

    body = _render()
    while len(body) > max_chars and sections:
        sections.pop()
        tail = _recompute_section_tail(
            sidecar_uri=sidecar_uri,
            original_row_count=original_rows,
            visible_rows=len(sections),
        )
        body = _render()
    if len(body) > max_chars:
        return f"{base_body}\n{sha_line}"[:max_chars].rstrip()
    return body


def format_closeout_memo_bus_delivery(
    *,
    memo_kind: str,
    memo_status: str,
    wake_lane: str,
    dispatch_id: str,
    execution_id: str | None,
    sidecar_uri: str | None,
    sidecar_text: str,
    spill_uri: str | None = None,
    allow_long_body: bool = False,
) -> CloseoutMemoBusDelivery:
    """Compact bus body for closeout-memo overflow / fallback posts."""
    uri = (sidecar_uri or spill_uri or "").strip() or None
    if not uri:
        if len(sidecar_text) <= MAX_BODY_CHARS:
            return CloseoutMemoBusDelivery(body=sidecar_text)
        if allow_long_body:
            return CloseoutMemoBusDelivery(body=sidecar_text, allow_long_body=True)
        return CloseoutMemoBusDelivery(
            body=_compact_closeout_pointer(
                memo_kind=memo_kind,
                memo_status=memo_status,
                wake_lane=wake_lane,
                dispatch_id=dispatch_id,
                execution_id=execution_id,
                sidecar_uri="agent-bus:sidecar-content",
                sidecar_text=sidecar_text,
            ),
            sidecar_content=sidecar_text,
        )
    digest = hashlib.sha256(sidecar_text.encode("utf-8")).hexdigest()
    section_map, hidden = build_section_map(sidecar_text)
    original_rows = len(section_map) + hidden
    tail: str | None = None
    if hidden:
        rel = uri.removeprefix("cortex://")
        tail = f"+{hidden} more (md_list {rel})"
    context = [
        f"lane: {wake_lane}",
        f"kind: {memo_kind} status: {memo_status}",
    ]
    return CloseoutMemoBusDelivery(
        body=compact_pointer_body(
            verdict=f"CLOSEOUT {memo_status.upper()}",
            execution_id=execution_id,
            dispatch_id=dispatch_id,
            sidecar_uri=uri,
            sidecar_sha256=digest,
            section_map=section_map,
            context_lines=context,
            section_tail=tail,
            section_map_source_rows=original_rows,
        )
    )


def _compact_closeout_pointer(
    *,
    memo_kind: str,
    memo_status: str,
    wake_lane: str,
    dispatch_id: str,
    execution_id: str | None,
    sidecar_uri: str,
    sidecar_text: str,
) -> str:
    digest = hashlib.sha256(sidecar_text.encode("utf-8")).hexdigest()
    section_map, hidden = build_section_map(sidecar_text)
    original_rows = len(section_map) + hidden
    tail: str | None = None
    if hidden:
        rel = sidecar_uri.removeprefix("cortex://")
        tail = f"+{hidden} more (md_list {rel})"
    context = [
        f"lane: {wake_lane}",
        f"kind: {memo_kind} status: {memo_status}",
    ]
    return compact_pointer_body(
        verdict=f"CLOSEOUT {memo_status.upper()}",
        execution_id=execution_id,
        dispatch_id=dispatch_id,
        sidecar_uri=sidecar_uri,
        sidecar_sha256=digest,
        section_map=section_map,
        context_lines=context,
        section_tail=tail,
        section_map_source_rows=original_rows,
    )


def format_closeout_memo_bus_body(
    *,
    memo_kind: str,
    memo_status: str,
    wake_lane: str,
    dispatch_id: str,
    execution_id: str | None,
    sidecar_uri: str | None,
    sidecar_text: str,
    spill_uri: str | None = None,
    allow_long_body: bool = False,
) -> str:
    """Compact bus body string (see ``format_closeout_memo_bus_delivery`` for spill)."""
    return format_closeout_memo_bus_delivery(
        memo_kind=memo_kind,
        memo_status=memo_status,
        wake_lane=wake_lane,
        dispatch_id=dispatch_id,
        execution_id=execution_id,
        sidecar_uri=sidecar_uri,
        sidecar_text=sidecar_text,
        spill_uri=spill_uri,
        allow_long_body=allow_long_body,
    ).body
