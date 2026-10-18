"""Harvest preamble for cursor-sdk closeout sidecars.

The first section maestro reads is the preamble before the first ATX heading,
capped at 4500 characters. These helpers keep line-start status, VERDICT, and
residue inside that window. Moved out of cursor_sdk_deliverables.
"""

from __future__ import annotations

import json
import re

# Maestro step 2 reads the preamble before the first ATX heading, capped at
# 4500 characters (cdp-operator-proxy: fs read ≤4500; a longer first section
# falls through to a line-start VERDICT/status search and skips
# structured_closeout_full). A verbatim model body can hide those fields past
# that window — specimen closeout d40fa4c3b9c3-8dcc4e70.md, 153153-char preamble.
_HARVEST_SECTION_CAP = 4500
_HARVEST_PREAMBLE_MAX_LINES = 60
_CLOSEOUT_BODY_HEADING = "## closeout_body"
_UNSTATED_STATUS_LINE = "status: unstated"
_EMPTY_RESIDUE_LINE = "residue: none"
_STATUS_LINE_RE = re.compile(r"(?m)^(status:[^\n]*)$")
_VERDICT_LINE_RE = re.compile(r"(?m)^(VERDICT:[^\n]*)$")
_RESIDUE_LINE_RE = re.compile(r"(?m)^(residue:[^\n]*)$")
_ATX_HEADING_RE = re.compile(r"(?m)^#{1,6} ")


def _first_atx_offset(text: str) -> int | None:
    match = _ATX_HEADING_RE.search(text)
    return None if match is None else match.start()


_FENCE_MARK_RE = re.compile(r"(?m)^```")
_STATUS_TOKENS = frozenset(
    {"complete", "partial", "failed", "gated", "degraded", "blocked"}
)


def _fenced_spans(text: str) -> tuple[tuple[int, int], ...]:
    marks = [match.start() for match in _FENCE_MARK_RE.finditer(text)]
    spans: list[tuple[int, int]] = []
    for index in range(0, len(marks), 2):
        end = marks[index + 1] if index + 1 < len(marks) else len(text)
        spans.append((marks[index], end))
    return tuple(spans)


def _unfenced_line_starts(pattern: re.Pattern[str], text: str) -> list[str]:
    spans = _fenced_spans(text)
    hits: list[str] = []
    for match in pattern.finditer(text):
        if any(start <= match.start() < end for start, end in spans):
            continue
        hits.append(match.group(1))
    return hits


def _unique_line(hits: list[str]) -> str | None:
    distinct = list(dict.fromkeys(hits))
    if len(distinct) == 1:
        return distinct[0]
    return None


def _accepted_status_line(line: str) -> bool:
    value = line.split(":", 1)[1].strip().lower()
    token = value.split()[0].split(":")[0] if value else ""
    return token in _STATUS_TOKENS


def _rewrite_rejected_leading_status(section: str) -> str:
    if not section.startswith("status:"):
        return section
    leading, sep, rest = section.partition("\n")
    if _accepted_status_line(leading):
        return section
    return _UNSTATED_STATUS_LINE + (sep + rest if sep else "\n")


def _harvest_field_lines(content: str) -> tuple[str, str | None, str]:
    """Status, optional VERDICT, and residue to place in the harvest preamble.

    A leading ``status:`` line is kept only when its token is allowed.
    ``full_result_text``'s ``status: degraded`` qualifies. A non-token leading
    line becomes ``status: unstated`` so the receipt stamp can replace it.
    A single unfenced line is copied up. Fenced lines are ignored.
    Distinct unfenced hits are not collapsed to the last one: status and
    residue fall back to sentinels the receipt can replace, and VERDICT is
    omitted so a quoted artifact cannot become the only line maestro reads.
    """
    if content.startswith("status:"):
        leading = content.split("\n", 1)[0]
        status = leading if _accepted_status_line(leading) else _UNSTATED_STATUS_LINE
    else:
        candidate = _unique_line(_unfenced_line_starts(_STATUS_LINE_RE, content))
        status = (
            candidate
            if candidate and _accepted_status_line(candidate)
            else _UNSTATED_STATUS_LINE
        )
    verdict = _unique_line(_unfenced_line_starts(_VERDICT_LINE_RE, content))
    residue = (
        _unique_line(_unfenced_line_starts(_RESIDUE_LINE_RE, content))
        or _EMPTY_RESIDUE_LINE
    )
    return status, verdict, residue


def _missing_harvest_lines(section: str, content: str) -> list[str]:
    status, verdict, residue = _harvest_field_lines(content)
    missing: list[str] = []
    if _STATUS_LINE_RE.search(section) is None:
        missing.append(status)
    if verdict and _VERDICT_LINE_RE.search(section) is None:
        missing.append(verdict)
    if _RESIDUE_LINE_RE.search(section) is None:
        missing.append(residue)
    return missing


def _fit_preamble(lines: list[str]) -> str:
    block = "\n".join(lines) + "\n"
    if len(block) <= _HARVEST_SECTION_CAP:
        return block
    clipped = block[: _HARVEST_SECTION_CAP - 1]
    if clipped.endswith("\n"):
        return clipped
    return clipped + "\n"


def _splice_harvest_lines(
    content: str,
    section: str,
    heading_at: int | None,
    missing: list[str],
) -> str | None:
    extra = "\n".join(missing) + "\n"
    base = section
    if base and not base.endswith("\n"):
        base += "\n"
    if base and not base.endswith("\n\n"):
        base += "\n"
    merged = base + extra
    if len(merged) > _HARVEST_SECTION_CAP:
        return None
    if heading_at is None:
        return merged
    return merged + content[heading_at:]


def bound_closeout_harvest_preamble(content: str) -> str:
    """Keep line-start harvest fields inside a first section of at most 4500 chars.

    The first section is the preamble before the first ATX heading. When that
    preamble already fits and already carries status, residue, and VERDICT
    (only if the model emitted one), the bytes are unchanged. A short preamble
    that is only missing lines gets those lines appended. An allowed
    ``status:`` prefix stays at column 0. A non-token leading line becomes
    ``status: unstated``. A preamble over the cap is closed
    with ``## closeout_body`` and the original bytes follow that heading.
    """
    heading_at = _first_atx_offset(content)
    raw_section = content if heading_at is None else content[:heading_at]
    section = _rewrite_rejected_leading_status(raw_section)
    normalized = (
        content
        if section == raw_section
        else section
        if heading_at is None
        else section + content[heading_at:]
    )
    missing = _missing_harvest_lines(section, normalized)
    if len(section) <= _HARVEST_SECTION_CAP and not missing:
        return normalized
    if len(section) <= _HARVEST_SECTION_CAP and missing:
        spliced = _splice_harvest_lines(normalized, section, heading_at, missing)
        if spliced is not None:
            return spliced
    status, verdict, residue = _harvest_field_lines(normalized)
    lines = [status]
    if verdict:
        lines.append(verdict)
    lines.append(residue)
    preamble = _fit_preamble(lines)
    if normalized.startswith(f"{preamble}{_CLOSEOUT_BODY_HEADING}"):
        return normalized
    return f"{preamble}{_CLOSEOUT_BODY_HEADING}\n\n{content}"


def _replace_exact_line(section: str, old: str, new: str) -> str:
    lines = section.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.rstrip("\r\n") == old:
            newline = "\n" if line.endswith("\n") else ""
            lines[index] = new + newline
            break
    return "".join(lines)


def _clip_harvest_section(section: str) -> str:
    lines = section.splitlines(keepends=True)
    if len(lines) > _HARVEST_PREAMBLE_MAX_LINES:
        section = "".join(lines[:_HARVEST_PREAMBLE_MAX_LINES])
    if len(section) <= _HARVEST_SECTION_CAP:
        return section
    clipped = section[: _HARVEST_SECTION_CAP - 1]
    if clipped.endswith("\n"):
        return clipped
    return clipped + "\n"


def _render_residue_line(items: object) -> str | None:
    if isinstance(items, str):
        parts = [items.strip()] if items.strip() else []
    elif isinstance(items, list):
        parts = [str(item).strip() for item in items if str(item).strip()]
    else:
        return None
    if not parts:
        return None
    return "residue: " + "; ".join(part.replace("\n", " ") for part in parts)


def stamp_harvest_status_from_structured(existing: str, full_body: str) -> str:
    """Replace harvest sentinels with the structured closeout's status and residue.

    Model text that never emitted line-start ``status:`` is stored as
    ``status: unstated``. The receipt JSON is the status the harvester must
    see; ``## structured_closeout_full`` itself is excluded from that read.
    """
    payload = json.loads(full_body)
    if not isinstance(payload, dict):
        return existing
    heading_at = _first_atx_offset(existing)
    section = existing if heading_at is None else existing[:heading_at]
    rest = "" if heading_at is None else existing[heading_at:]
    status = payload.get("status")
    if (
        isinstance(status, str)
        and status.strip()
        and _UNSTATED_STATUS_LINE in section.splitlines()
    ):
        section = _replace_exact_line(
            section, _UNSTATED_STATUS_LINE, f"status: {status.strip()}"
        )
    residue_line = _render_residue_line(payload.get("propagation_residue"))
    if residue_line and _EMPTY_RESIDUE_LINE in section.splitlines():
        section = _replace_exact_line(section, _EMPTY_RESIDUE_LINE, residue_line)
    return _clip_harvest_section(section) + rest


def _non_fenced_summary_lines(body: str, *, max_lines: int) -> list[str]:
    lines = body.splitlines()
    out: list[str] = []
    in_fence = False
    for line in lines:
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        stripped = line.strip()
        if _ATX_HEADING_RE.match(line):
            continue
        if _STATUS_LINE_RE.match(line) or _VERDICT_LINE_RE.match(line):
            continue
        if _RESIDUE_LINE_RE.match(line):
            continue
        if not stripped:
            if out and out[-1].strip():
                out.append(line)
        else:
            out.append(line)
        if len(out) >= max_lines:
            break
    return out


def partition_closeout_executive_envelope(
    body: str,
    *,
    status_fallback: str | None = None,
    residue_fallback: str | None = None,
) -> tuple[str, str | None]:
    """Split oversize closeout prose into an executive header and optional archive body."""
    content = body or ""
    status, verdict, residue = _harvest_field_lines(content)
    if status_fallback and status == _UNSTATED_STATUS_LINE:
        status = status_fallback
    if residue_fallback and residue == _EMPTY_RESIDUE_LINE:
        residue = residue_fallback
    heading_at = _first_atx_offset(content)
    raw_section = content if heading_at is None else content[:heading_at]
    line_count = len(content.splitlines())
    fits = (
        line_count <= _HARVEST_PREAMBLE_MAX_LINES
        and len(content) <= _HARVEST_SECTION_CAP
        and len(raw_section) <= _HARVEST_SECTION_CAP
    )
    if fits:
        bounded = bound_closeout_harvest_preamble(content)
        return bounded, None
    header_lines = [status]
    if verdict:
        header_lines.append(verdict)
    header_lines.append(residue)
    header_lines.append("")
    header_lines.extend(_non_fenced_summary_lines(content, max_lines=54))
    header = "\n".join(header_lines).strip()
    header_line_count = len(header.splitlines())
    if header_line_count > 58:
        header = "\n".join(header.splitlines()[:58])
    if len(header) > _HARVEST_SECTION_CAP:
        header = header[: _HARVEST_SECTION_CAP - 1] + "\n"
    if not header.endswith("\n"):
        header = header + "\n"
    return header, content
