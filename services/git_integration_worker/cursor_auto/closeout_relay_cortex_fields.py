"""Heading / bold-field extraction for CLOSEOUT judgment cells."""

from __future__ import annotations

import re

_FIELD_HEADING_ALIASES: dict[str, tuple[str, ...]] = {
    "status_claim": ("status_claim", "status claim"),
    # Legacy §2 heading — claim extraction only; not envelope ``status:`` measurement.
    "status": ("status",),
    "ac_verdict": (
        "ac_verdict",
        "ac verdict",
        "verdict",
        "ac1 per-site disposition",
    ),
    "deltas_to_spec": (
        "deltas_to_spec",
        "deltas to spec",
        "delta to spec",
        "scope delta",
        "scope_delta",
    ),
    "decisions_taken": ("decisions_taken", "decisions taken"),
    "next": ("next", "next steps"),
    "open forks": ("open_forks", "open forks"),
    "effects": ("effects",),
    "evidence": ("evidence",),
    "access": ("access",),
    "coverage": ("coverage",),
    "model_actual": ("model actual", "model_actual", "modelactual"),
    "checkpoint_claim": ("checkpoint_claim", "checkpoint claim"),
    # Legacy §2 heading — claim extraction only; not the infra ``checkpoint:`` line.
    "checkpoint": ("checkpoint",),
}

_TABLE_ROW_RE = re.compile(
    r"^\|\s*(?P<field>[^|]+?)\s*\|\s*(?P<value>.*?)\s*\|\s*$",
    re.MULTILINE,
)
_TABLE_SEP_RE = re.compile(r"^\|\s*[-:]+\s*\|")

_ATX_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
_BOLD_FIELD_LINE_RE = re.compile(
    r"(?im)^\*\*(?P<heading>[^*\n]+?):\*\*[ \t]*(?P<rest>.*)$",
)
_BOLD_HEADING_ONLY_RE = re.compile(
    r"(?im)^\*\*(?P<heading>[^*\n]+?)\*\*\s*$",
)
_PLAIN_FIELD_LINE_RE = re.compile(
    r"(?im)^(?P<field>[a-z][a-z0-9_ ]*?)\s*:\s*(?P<rest>.*)$",
)
_FENCE_OPEN_RE = re.compile(r"^[ \t]*(?P<marker>`{3,}|~{3,})")


def fenced_spans(body: str) -> tuple[tuple[int, int], ...]:
    """Return source offsets enclosed by Markdown backtick or tilde fences.

    Unterminated fences intentionally extend to the end of the authored body:
    control-token discovery must not trust content after an unclosed code fence.
    """
    spans: list[tuple[int, int]] = []
    fence_start: int | None = None
    marker_char = ""
    marker_width = 0
    offset = 0
    for line in body.splitlines(keepends=True):
        match = _FENCE_OPEN_RE.match(line)
        if match is not None:
            marker = match.group("marker")
            if fence_start is None:
                fence_start = offset
                marker_char = marker[0]
                marker_width = len(marker)
            elif marker[0] == marker_char and len(marker) >= marker_width:
                spans.append((fence_start, offset + len(line)))
                fence_start = None
                marker_char = ""
                marker_width = 0
        offset += len(line)
    if fence_start is not None:
        spans.append((fence_start, len(body)))
    return tuple(spans)


def in_fenced_span(spans: tuple[tuple[int, int], ...], offset: int) -> bool:
    """Return whether a candidate control token starts within a fenced region."""
    return any(start <= offset < end for start, end in spans)


def _normalize_heading_key(text: str) -> str:
    text = re.sub(r"\s*\([^)]*\)", "", text)
    text = re.sub(r"[*`_]+", "", text)
    return re.sub(r"[_\-\s]+", "", text.casefold())


def _ac_subsection_heading(normalized_heading: str) -> bool:
    """True when *normalized_heading* is an AC-n subsection label, not §2 ac_verdict."""
    return bool(re.fullmatch(r"ac\d+.*", normalized_heading))


def _heading_matches_field(
    heading: str, field: str, *, exact_only: bool = False
) -> bool:
    """Match an authored heading to a §2 field.

    Prefix matching is what lets ``Next steps`` bind to ``next``, but it also lets
    ``AC1 — …`` bind to ``ac_verdict``. When both an exact and a prefix match exist
    in one document the exact one is the author's intent, so callers run an
    ``exact_only`` pass first.
    """
    normalized_heading = _normalize_heading_key(heading)
    if field == "ac_verdict" and _ac_subsection_heading(normalized_heading):
        return False
    for alias in _FIELD_HEADING_ALIASES[field]:
        normalized_alias = _normalize_heading_key(alias)
        if normalized_heading == normalized_alias:
            return True
        if not exact_only and normalized_heading.startswith(normalized_alias):
            return True
    return False


def _table_heading_matches_field(heading: str, field: str) -> bool:
    """Strict table-row field match — reject AC1-style verdict rows."""
    normalized_heading = _normalize_heading_key(heading)
    if re.fullmatch(r"ac\d+", normalized_heading):
        return False
    if normalized_heading == _normalize_heading_key(field):
        return True
    for alias in _FIELD_HEADING_ALIASES[field]:
        normalized_alias = _normalize_heading_key(alias)
        if normalized_alias == "ac1":
            continue
        if normalized_heading == normalized_alias:
            return True
    return False


def _extract_atx_section(
    body: str, field: str, *, exact_only: bool = False
) -> str | None:
    spans = fenced_spans(body)
    matches = [
        match
        for match in _ATX_HEADING_RE.finditer(body)
        if not in_fenced_span(spans, match.start())
    ]
    for index, match in enumerate(matches):
        heading = match.group(2).strip()
        if not _heading_matches_field(heading, field, exact_only=exact_only):
            continue
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        section = body[start:end].strip()
        return section or None
    return None


def _normalize_field_heading(raw: str) -> str:
    return raw.strip().rstrip(":").strip()


def _canonical_field_for_plain_heading(raw_field: str) -> str | None:
    """Map a plain ``field:`` line prefix to the canonical §2 field key."""
    normalized = _normalize_heading_key(raw_field)
    for canonical, aliases in _FIELD_HEADING_ALIASES.items():
        for alias in aliases:
            if _normalize_heading_key(alias) == normalized:
                return canonical
    return None


def _plain_field_line_spans(body: str) -> list[tuple[int, int, str, str]]:
    """Collect plain ``field: rest`` §2 lines (reporting-contract inline format)."""
    spans: list[tuple[int, int, str, str]] = []
    fenced = fenced_spans(body)
    offset = 0
    for line in body.splitlines(keepends=True):
        stripped = line.strip()
        if in_fenced_span(fenced, offset) or not stripped or stripped.startswith("#"):
            offset += len(line)
            continue
        match = _PLAIN_FIELD_LINE_RE.match(stripped)
        if match is None:
            offset += len(line)
            continue
        canonical = _canonical_field_for_plain_heading(match.group("field"))
        if canonical is None:
            offset += len(line)
            continue
        rest = match.group("rest").strip()
        spans.append((offset, offset + len(line), canonical, rest))
        offset += len(line)
    return spans


def _is_canonical_section2_heading(heading: str) -> bool:
    """True when *heading* names a §2 relay field (not nested subsection labels)."""
    normalized_heading = _normalize_heading_key(heading)
    if re.fullmatch(r"ac\d+.*", normalized_heading):
        return False
    return any(
        _heading_matches_field(heading, field, exact_only=True)
        for field in _FIELD_HEADING_ALIASES
    )


def _canonical_bold_field_spans(body: str) -> list[tuple[int, int, str, str]]:
    """Bold §2 field lines only — excludes nested ``**AC-n …:**`` subsection labels."""
    return [
        span
        for span in _field_line_spans(body)
        if _is_canonical_section2_heading(span[2])
    ]


def _next_field_boundary(body: str, start: int) -> int:
    for line_start, _, _, _ in _plain_field_line_spans(body):
        if line_start >= start:
            return line_start
    for line_start, _, _, _ in _canonical_bold_field_spans(body):
        if line_start >= start:
            return line_start
    fenced = fenced_spans(body)
    for match in _ATX_HEADING_RE.finditer(body):
        if match.start() >= start and not in_fenced_span(fenced, match.start()):
            return match.start()
    return len(body)


def _extract_plain_same_line(body: str, field: str) -> str | None:
    for _start, _end, heading, rest in _plain_field_line_spans(body):
        if heading == field and rest:
            return rest
    return None


def _extract_plain_section(body: str, field: str) -> str | None:
    offset = 0
    fenced = fenced_spans(body)
    for line in body.splitlines(keepends=True):
        if in_fenced_span(fenced, offset):
            offset += len(line)
            continue
        stripped = line.strip()
        match = _PLAIN_FIELD_LINE_RE.match(stripped) if stripped else None
        if match is not None:
            canonical = _canonical_field_for_plain_heading(match.group("field"))
            if canonical == field:
                rest = match.group("rest").strip()
                if rest:
                    return rest
                start = offset + len(line)
                end = _next_field_boundary(body, start)
                section = body[start:end].strip()
                return section or None
        offset += len(line)
    return None


def field_heading_present(body: str, field: str) -> bool:
    """True when a §2 field heading exists in authored prose."""
    for _start, _end, heading, _rest in _plain_field_line_spans(body):
        if heading == field:
            return True
    for _start, _end, heading, _rest in _field_line_spans(body):
        if _heading_matches_field(heading, field):
            return True
    fenced = fenced_spans(body)
    for match in _ATX_HEADING_RE.finditer(body):
        if not in_fenced_span(fenced, match.start()) and _heading_matches_field(
            match.group(2).strip(), field
        ):
            return True
    return extract_table_field(body, field) is not None


def _field_line_spans(body: str) -> list[tuple[int, int, str, str]]:
    """Collect bold §2 field lines — ``**field:** rest`` and ``**field**`` heading-only."""
    spans: list[tuple[int, int, str, str]] = []
    fenced = fenced_spans(body)
    for match in _BOLD_FIELD_LINE_RE.finditer(body):
        if in_fenced_span(fenced, match.start()):
            continue
        heading = _normalize_field_heading(match.group("heading"))
        rest = match.group("rest").strip()
        spans.append((match.start(), match.end(), heading, rest))
    colon_starts = {start for start, _, _, _ in spans}
    for match in _BOLD_HEADING_ONLY_RE.finditer(body):
        if in_fenced_span(fenced, match.start()) or match.start() in colon_starts:
            continue
        heading = _normalize_field_heading(match.group("heading"))
        spans.append((match.start(), match.end(), heading, ""))
    spans.sort(key=lambda item: item[0])
    return spans


def _extract_bold_same_line(
    body: str, field: str, *, exact_only: bool = False
) -> str | None:
    for _start, _end, heading, rest in _field_line_spans(body):
        if _heading_matches_field(heading, field, exact_only=exact_only) and rest:
            return rest
    return None


def _extract_bold_section(
    body: str, field: str, *, exact_only: bool = False
) -> str | None:
    lines = body.splitlines(keepends=True)
    fenced = fenced_spans(body)
    offset = 0
    for line in lines:
        line_start = offset
        offset += len(line)
        if in_fenced_span(fenced, line_start):
            continue
        colon_match = _BOLD_FIELD_LINE_RE.match(line)
        heading_only_match = None if colon_match else _BOLD_HEADING_ONLY_RE.match(line)
        match = colon_match or heading_only_match
        if match is None:
            continue
        heading = _normalize_field_heading(match.group("heading"))
        if not _heading_matches_field(heading, field, exact_only=exact_only):
            continue
        rest = match.group("rest").strip() if colon_match else ""
        if rest:
            return rest
        end = _next_field_boundary(body, offset)
        section = body[offset:end].strip()
        return section or None
    return None


def extract_table_field(body: str, field: str) -> str | None:
    """Extract a field value from an existing markdown table row."""
    spans = fenced_spans(body)
    for match in _TABLE_ROW_RE.finditer(body):
        if in_fenced_span(spans, match.start()):
            continue
        row_field = match.group("field").strip()
        if _TABLE_SEP_RE.match(f"|{row_field}|"):
            continue
        if _table_heading_matches_field(row_field, field):
            value = match.group("value").strip()
            return value or None
    return None


def extract_status(body: str) -> str | None:
    """Backward-compatible alias — prefer :func:`extract_status_claim`."""
    from services.git_integration_worker.cursor_auto.lane_a_status import (
        extract_status_claim,
    )

    return extract_status_claim(body)


def status_from_section2(text: str) -> str | None:
    """Backward-compatible alias — prefer :func:`status_claim_from_section2`."""
    from services.git_integration_worker.cursor_auto.lane_a_status import (
        status_claim_from_section2,
    )

    return status_claim_from_section2(text)


def extract_field_section(body: str, field: str) -> str | None:
    """Extract judgment-cell text for *field* from cortex prose headings.

    Runs the heading extractors twice: once accepting only an exact field
    heading, then once allowing prefix aliases. The loose pass is unchanged, so
    a field that resolved before still resolves — only *which* section wins can
    differ, and it differs toward the heading the author actually named.
    """
    table = extract_table_field(body, field)
    if table:
        return table
    plain_same = _extract_plain_same_line(body, field)
    if plain_same:
        return plain_same
    plain_section = _extract_plain_section(body, field)
    if plain_section:
        return plain_section
    for exact_only in (True, False):
        same_line = _extract_bold_same_line(body, field, exact_only=exact_only)
        if same_line is not None and same_line.strip():
            return same_line
        for extractor in (_extract_bold_section, _extract_atx_section):
            section = extractor(body, field, exact_only=exact_only)
            if section:
                return section
    return None


__all__ = [
    "extract_field_section",
    "extract_status",
    "extract_table_field",
    "field_heading_present",
    "fenced_spans",
    "in_fenced_span",
    "status_from_section2",
]
