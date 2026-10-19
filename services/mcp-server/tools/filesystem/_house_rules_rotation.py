"""Single-call house-rules rotation into the rulings archive."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from tools._durable_write import durable_write_text
from tools._hashing import format_sha256_uri

from ._write_authority import HOUSE_RULES_MAX_BYTES, RULINGS_ARCHIVE_URI

_HOUSE_RULES_SECTION = "HOUSE RULES"
_RULING_LINE = re.compile(r"^- (K\d+|a:\d+|\|)")


@dataclass(frozen=True)
class HouseRulesRotationResult:
    trimmed_text: str
    rotated_rows: list[str]
    archive_uri: str
    archive_written_sha256: str


def _split_house_rules_section(text: str) -> tuple[str, str, str]:
    marker = f"## {_HOUSE_RULES_SECTION}"
    idx = text.find(marker)
    if idx < 0:
        return text, "", ""
    after_marker = idx + len(marker)
    rest = text[after_marker:]
    if rest.startswith("\r\n"):
        body_start = after_marker + 2
    elif rest.startswith("\n"):
        body_start = after_marker + 1
    else:
        body_start = after_marker
    next_heading = re.search(r"\n##[^#]", text[body_start:])
    if next_heading:
        section_end = body_start + next_heading.start() + 1
    else:
        section_end = len(text)
    prefix = text[:body_start]
    section_body = text[body_start:section_end]
    suffix = text[section_end:]
    return prefix, section_body, suffix


def _ruling_line_indices(section_body: str) -> list[int]:
    lines = section_body.splitlines(keepends=True)
    indices: list[int] = []
    for i, line in enumerate(lines):
        if _RULING_LINE.match(line.rstrip("\r\n")):
            indices.append(i)
    return indices


def _ruling_k_number(line: str) -> int | None:
    match = re.match(r"^- K(\d+)", line.strip())
    if not match:
        return None
    return int(match.group(1))


def _oldest_ruling_line_index(lines: list[str], indices: list[int]) -> int | None:
    scored: list[tuple[int, int]] = []
    for idx in indices:
        kid = _ruling_k_number(lines[idx])
        if kid is not None:
            scored.append((kid, idx))
    if scored:
        return min(scored, key=lambda pair: pair[0])[1]
    return indices[-1] if indices else None


def _trim_oldest_rulings(text: str, *, limit_bytes: int) -> tuple[str, list[str]]:
    rotated: list[str] = []
    current = text
    while len(current.encode("utf-8")) > limit_bytes:
        prefix, section_body, suffix = _split_house_rules_section(current)
        if not section_body:
            break
        lines = section_body.splitlines(keepends=True)
        indices = _ruling_line_indices(section_body)
        drop_idx = _oldest_ruling_line_index(lines, indices)
        if drop_idx is None:
            break
        row = lines.pop(drop_idx)
        rotated.append(row.rstrip("\r\n"))
        section_body = "".join(lines)
        current = prefix + section_body + suffix
    return current, rotated


def _format_archive_block(row: str) -> str:
    heading = row.strip()
    kid = "rotated"
    match = re.match(r"^- (K\d+)", heading)
    if match:
        kid = match.group(1)
    return f"\n#### {kid} — house-rules rotation\n{row.strip()}\n"


def rotate_house_rules_to_fit(
    *,
    house_rules_text: str,
    archive_path: Path,
    limit_bytes: int = HOUSE_RULES_MAX_BYTES,
) -> HouseRulesRotationResult | None:
    """Archive oldest ruling rows until *house_rules_text* is within *limit_bytes*."""
    if len(house_rules_text.encode("utf-8")) <= limit_bytes:
        return None
    trimmed, rotated = _trim_oldest_rulings(house_rules_text, limit_bytes=limit_bytes)
    if not rotated or len(trimmed.encode("utf-8")) > limit_bytes:
        return None
    append_body = "".join(_format_archive_block(row) for row in rotated)
    prior = archive_path.read_text(encoding="utf-8") if archive_path.is_file() else ""
    archive_image = prior + append_body
    try:
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        archive_written_sha256 = durable_write_text(archive_path, archive_image)
    except OSError:
        return None
    return HouseRulesRotationResult(
        trimmed_text=trimmed,
        rotated_rows=rotated,
        archive_uri=RULINGS_ARCHIVE_URI,
        archive_written_sha256=format_sha256_uri(archive_written_sha256),
    )
