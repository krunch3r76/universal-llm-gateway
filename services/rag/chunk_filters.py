"""Noise chunk detection for index-time tagging and retrieval fallback.

**Noise** (``is_noise`` in Chroma metadata) means content we intentionally deprioritize
for retrieval and skip during knowledge extraction:

- **citation_block**: Mostly bibliography / reference lines (citation-shaped lines dominate).
- **dense_table**: HTML table markup or tabular rows with little surrounding prose
  (e.g. scraped price grids).
- **garbled_extraction**: OCR-like junk lines, reference markers, or empty chunk.
- **boilerplate**: URL/link-dump lines, license-ish patterns matched by junk-line heuristics.
- **legacy_bibliography**: legacy rows had ``is_bibliography`` only; normalized to ``is_noise`` at index time.
- **unspecified_noise**: ``is_noise`` true without a finer ``noise_reason`` (e.g. classifier output).

Extraction *failures* (model/parse errors) are not noise; they stay in ``failed_extractions``.

Shared by RAG service (indexing) and pipeline retrieve handlers so rules stay aligned.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

# Stored in Chroma when only legacy ``is_bibliography`` was set (no heuristic reason).
NOISE_REASON_LEGACY_BIBLIOGRAPHY: str = "legacy_bibliography"
# Stored when ``is_noise`` is true without a populated ``noise_reason`` (e.g. LLM path).
NOISE_REASON_UNSPECIFIED: str = "unspecified_noise"

_JUNK_LINE_RE: re.Pattern[str] = re.compile(
    r"^\s*("
    r"\[\d+\]"
    r"|\d+\.\s+[A-Z]"
    r"|\d+\s+[`\[]https?://"  # Pattern A: bare number + space + URL/link
    r"|References\b|Bibliography\b"
    r"|Table\s+\d+"
    r"|\.\s+\.\s+\."
    r")"
)

_CITATION_LINE_RE: re.Pattern[str] = re.compile(
    r".*,\s*(?:19|20)\d{2}\b.*|.*\bet\s+al\.?\s*[,\.].*",
    re.IGNORECASE,
)

_URL_DENSE_THRESHOLD: float = 0.40

# HTML / ASCII table density — minimal prose relative to structure
_MIN_LINES_FOR_TABLE_HEURISTIC: int = 5
_TABLE_PIPE_MIN_PARTS: int = 4
_TABLE_PIPE_LINE_FRACTION: float = 0.45
_HTML_TD_TRIGGER: int = 8
_HTML_TR_TRIGGER: int = 5
_LONG_PROSE_WORDS: int = 15
_LONG_PROSE_LINE_FRACTION_MAX: float = 0.25
_MIN_PROSE_LINES_ESCAPE: int = 3


def _is_link_only_line(line: str) -> bool:
    """True when a line is predominantly a URL or markdown link."""
    stripped = line.strip().strip("`")
    if re.match(r"https?://\S+$", stripped):
        return True
    m = re.match(r"\[([^\]]*)\]\((https?://[^)]+)\)", stripped)
    if m:
        return len(m.group(1)) < 20
    return False


def is_citation_heavy(content: str, threshold: float = 0.25) -> bool:
    """True when >= threshold fraction of non-blank lines look like citations."""
    lines = [line for line in content.splitlines() if line.strip()]
    if not lines:
        return False
    citation_count = sum(1 for line in lines if _CITATION_LINE_RE.search(line))
    return (citation_count / len(lines)) >= threshold


def _looks_like_dense_table(content: str) -> bool:
    """True when content is purely table markup with negligible prose.

    Two guards prevent false positives on chunks mixing tables with analysis:
    * Absolute floor: ≥ ``_MIN_PROSE_LINES_ESCAPE`` long prose lines → always safe.
    * Ratio cap: long prose lines ≤ 25 % of total lines.
    Both must be satisfied before tagging.
    """
    lowered = content.lower()
    lines = [line for line in content.splitlines() if line.strip()]
    if not lines:
        return False

    long_prose = sum(1 for ln in lines if len(ln.split()) >= _LONG_PROSE_WORDS)
    if long_prose >= _MIN_PROSE_LINES_ESCAPE:
        return False

    td_n = lowered.count("<td")
    tr_n = lowered.count("<tr")
    if td_n >= _HTML_TD_TRIGGER or tr_n >= _HTML_TR_TRIGGER:
        if long_prose <= max(1, int(len(lines) * _LONG_PROSE_LINE_FRACTION_MAX)):
            return True

    if len(lines) < _MIN_LINES_FOR_TABLE_HEURISTIC:
        return False

    pipe_rows = sum(
        1
        for ln in lines
        if len([p for p in ln.split("|") if p.strip()]) >= _TABLE_PIPE_MIN_PARTS
    )
    tab_rows = sum(1 for ln in lines if ln.count("\t") >= 2)
    tabular = pipe_rows + tab_rows
    if tabular / len(lines) >= _TABLE_PIPE_LINE_FRACTION:
        if long_prose <= max(1, int(len(lines) * _LONG_PROSE_LINE_FRACTION_MAX)):
            return True

    return False


def noise_reason(content: str, threshold: float = 0.35) -> str | None:
    """Return a noise category string, or None if the chunk is not treated as noise."""
    lines = [line for line in content.splitlines() if line.strip()]
    if not lines:
        return "garbled_extraction"
    if _looks_like_dense_table(content):
        return "dense_table"
    if is_citation_heavy(content, 0.25):
        return "citation_block"
    junk_count = sum(1 for line in lines if _JUNK_LINE_RE.search(line))
    if (junk_count / len(lines)) >= threshold:
        return "garbled_extraction"
    url_dense = sum(1 for ln in lines if _is_link_only_line(ln)) / len(lines)
    if url_dense >= _URL_DENSE_THRESHOLD:
        return "boilerplate"
    return None


def chunk_is_noise(content: str, threshold: float = 0.35) -> bool:
    """True when ``noise_reason`` is not None."""
    return noise_reason(content, threshold) is not None


def chunk_metadata_is_noise(meta: Mapping[str, object]) -> bool:
    """True if Chroma metadata marks the chunk as noise (new or legacy key)."""
    v = meta.get("is_noise")
    if v is True:
        return True
    legacy = meta.get("is_bibliography")
    return legacy is True


def normalize_noise_metadata(metadata: dict[str, Any]) -> None:
    """Align legacy bibliography flags with ``is_noise`` / ``noise_reason`` before upsert.

    Call after heuristic tagging (and any overrides) so hard-coded or DB-round-tripped
    ``is_bibliography`` still participates in extraction skip and retrieval filters.
    Drops ``noise_reason`` when the chunk is not noise.
    """
    if metadata.get("is_bibliography") is True:
        metadata["is_noise"] = True
    if chunk_metadata_is_noise(metadata):
        if not metadata.get("noise_reason"):
            metadata["noise_reason"] = (
                NOISE_REASON_LEGACY_BIBLIOGRAPHY
                if metadata.get("is_bibliography") is True
                else NOISE_REASON_UNSPECIFIED
            )
    else:
        metadata.pop("noise_reason", None)


__all__ = [
    "NOISE_REASON_LEGACY_BIBLIOGRAPHY",
    "NOISE_REASON_UNSPECIFIED",
    "chunk_is_noise",
    "chunk_metadata_is_noise",
    "is_citation_heavy",
    "noise_reason",
    "normalize_noise_metadata",
]
