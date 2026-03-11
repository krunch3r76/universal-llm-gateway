"""Bibliography/junk chunk detection for index-time tagging and retrieval fallback.

Shared by RAG service (indexing) and pipeline context_formatting (retrieve_assemble)
so both use the same rules.
"""

from __future__ import annotations

import re

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


def _is_link_only_line(line: str) -> bool:
    """True when a line is predominantly a URL or markdown link."""
    stripped = line.strip().strip("`")
    if re.match(r"https?://\S+$", stripped):
        return True
    m = re.match(r"\[([^\]]*)\]\((https?://[^)]+)\)", stripped)
    if m:
        return len(m.group(1)) < 20
    return False


def is_bibliography_heavy(content: str, threshold: float = 0.25) -> bool:
    """True when >= threshold fraction of non-blank lines look like citations."""
    lines = [line for line in content.splitlines() if line.strip()]
    if not lines:
        return False
    citation_count = sum(1 for line in lines if _CITATION_LINE_RE.search(line))
    return (citation_count / len(lines)) >= threshold


def chunk_is_junk(content: str, threshold: float = 0.35) -> bool:
    """True when content looks like a bibliography, table, or garbled PDF extraction."""
    lines = [line for line in content.splitlines() if line.strip()]
    if not lines:
        return True
    if is_bibliography_heavy(content, 0.25):
        return True
    junk_count = sum(1 for line in lines if _JUNK_LINE_RE.search(line))
    if (junk_count / len(lines)) >= threshold:
        return True
    url_dense = sum(1 for ln in lines if _is_link_only_line(ln)) / len(lines)
    return url_dense >= _URL_DENSE_THRESHOLD
