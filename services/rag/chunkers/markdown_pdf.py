"""Markdown and PDF chunking."""

from __future__ import annotations

import re
from collections import Counter

from markdown_sections import parse_sections

from services.rag.chunkers._sizing import _CHUNK_CHARS_PAD, _CHUNK_CHARS_TARGET, _LOG
from services.rag.chunkers.paragraph_utils import (
    Chunk,
    _annotate_chunk_indices,
    _char_upto_line,
    _split_paragraphs,
)


def chunk_markdown(
    path: str,
    content: str,
    *,
    target_chars: int = _CHUNK_CHARS_TARGET,
    pad_chars: int = _CHUNK_CHARS_PAD,
    overlap_paragraphs: int = 2,
) -> list[Chunk]:
    """Split markdown by headers, then paragraph-split within each section.

    Heading is prepended to every chunk in its section for extraction context;
    the heading prefix does not count toward the target/pad budget, but is
    included in overlap_prefix_len metadata for correct merge trimming.

    Parent sections with children yield only their intro text (content before
    the first child heading). Children get their own chunks via normal iteration.
    """

    def _add_chunks_from_section(section_text: str, heading_prefix: str) -> None:
        heading_str = f"## {heading_prefix}\n\n" if heading_prefix else ""
        for text, overlap_len in _split_paragraphs(
            section_text, target_chars, pad_chars, overlap_paragraphs
        ):
            full_text = heading_str + text if heading_str else text
            chunks.append(
                Chunk(
                    text=full_text,
                    metadata={
                        "source": source,
                        "heading": heading_prefix.strip(),
                        "section_path": heading_prefix.strip(),
                        "overlap_prefix_len": len(heading_str) + overlap_len,
                    },
                )
            )

    chunks: list[Chunk] = []
    source = path
    sections = parse_sections(content)
    lines = content.splitlines(keepends=True)

    for i, sec in enumerate(sections):
        first_child = (
            sections[i + 1]
            if i + 1 < len(sections) and sections[i + 1].level > sec.level
            else None
        )
        if first_child is not None:
            body_end = _char_upto_line(lines, first_child.line - 1)
            body = content[sec.start : body_end]
        else:
            body = content[sec.start : sec.end]

        if not body.strip():
            continue
        heading_prefix = sec.path or sec.heading
        heading_str = f"## {heading_prefix}\n\n" if heading_prefix else ""

        if len(body) <= target_chars:
            full_text = (
                f"## {heading_prefix}\n\n{body.strip()}"
                if heading_prefix
                else body.strip()
            )
            chunks.append(
                Chunk(
                    text=full_text,
                    metadata={
                        "source": source,
                        "heading": heading_prefix,
                        "section_path": heading_prefix,
                        "overlap_prefix_len": len(heading_str),
                    },
                )
            )
        else:
            _add_chunks_from_section(body, heading_prefix)

    return _annotate_chunk_indices(chunks)


_HEADER_MIN_PAGES = 3
_HEADER_MIN_RATIO = 0.3


def _strip_running_headers(
    pages: list[str],
    min_pages: int = _HEADER_MIN_PAGES,
    min_ratio: float = _HEADER_MIN_RATIO,
) -> list[str]:
    """Remove repeating running headers/footers from per-page markdown.

    Lines appearing on >= min_pages AND >= min_ratio of total pages are
    classified as running headers (paper title, author line, conference
    banner). Typically 2-5 unique lines per affected paper.
    """
    n = len(pages)
    if n < min_pages:
        return pages
    line_counts: Counter[str] = Counter()
    for page in pages:
        seen: set[str] = set()
        for line in page.splitlines():
            stripped = line.strip()
            if stripped and stripped not in seen:
                seen.add(stripped)
                line_counts[stripped] += 1
    threshold = max(min_pages, int(n * min_ratio))
    noise = {line for line, count in line_counts.items() if count >= threshold}
    if not noise:
        return pages
    cleaned: list[str] = []
    for page in pages:
        kept = [ln for ln in page.splitlines() if ln.strip() not in noise]
        cleaned.append("\n".join(kept))
    return cleaned


# ---------------------------------------------------------------------------
# PDF heading normalization (pymupdf4llm bold → ATX)
# ---------------------------------------------------------------------------

_NUMBERED_SEPARATE_RE = re.compile(
    r"^\*\*(?P<num>\d+(?:\.\d+)*)\*\*\s+"
    r"(?P<title>(?:\*\*[^*\n]+?\*\*\s*)+)\s*$",
    re.MULTILINE,
)
_NUMBERED_SINGLE_RE = re.compile(
    r"^\*\*(?P<num>\d+(?:\.\d+)*)\s+(?P<title>[^*\n]+?)\*\*\s*$",
    re.MULTILINE,
)
_ROMAN_SEPARATE_RE = re.compile(
    r"^(\*\*(?P<num>[IVXivx]+\.)\*\*)\s+(\*\*(?P<title>[^*\n]+?)\*\*)\s*$",
    re.MULTILINE,
)
_UNNUMBERED_ALONE_RE = re.compile(
    r"^\*\*(Abstract|References|Acknowledgments?|Appendix ?[A-Z]?"
    r"|Conclusion|Introduction|Related Work|Background|Discussion"
    r"|Evaluation|Experiments?|Methodology|Methods|Results|Future Work)\*\*\s*$",
    re.MULTILINE | re.IGNORECASE,
)
_ATX_BOLD_RE = re.compile(r"^(#{1,6}) \*\*(.+?)\*\*\s*$", re.MULTILINE)
_ADJACENT_BOLD_RE = re.compile(
    r"\*\*(?P<left>[^*\n]+?)\*\*(?P<ws>[ \t]+)\*\*(?P<right>[^*\n]+?)\*\*"
)
_CROSSLINE_HYPHEN_BOLD_RE = re.compile(
    r"\*\*(?P<left>[^*\n]+)-\*\*\s*\n[ \t]*\*\*(?P<right>[^*\n]+?)\*\*"
)
_BOLD_ONLY_LINE_RE = re.compile(r"^\*\*(?P<text>[^*\n]+?)\*\*\s*$")
_SENTENCE_TERMINAL_RE = re.compile(r"[.?!]\s*$")
_TABLE_OR_IMAGE_LINE_RE = re.compile(r"^(?:\||!\[)")
_MAX_PROMOTED_HEADING_CHARS = 120

_PREFIX_BLOCKLIST = re.compile(
    r"^(Table|Figure|Fig\.|Algorithm|Theorem|Lemma|Corollary"
    r"|Proof|Example|Definition|Remark|Note|Proposition)\b",
    re.IGNORECASE,
)
_ALGO_KEYWORD_RE = re.compile(
    r"^(for|if|else|elif|while|return|do|end|begin"
    r"|output|input|procedure|function)\b",
    re.IGNORECASE,
)


def _heading_depth(num_str: str) -> int:
    """Map section number depth to ATX heading level (1→h2, 1.1→h3, …, max h6)."""
    dots = num_str.rstrip(".").count(".")
    return min(dots + 2, 6)


def _merge_adjacent_bold_runs(markdown: str) -> str:
    """Collapse adjacent ``**...**`` runs separated only by inline whitespace."""
    merged = markdown
    while True:
        updated = _ADJACENT_BOLD_RE.sub(
            lambda m: (
                f"**{m.group('left').strip()}{m.group('ws')}"
                f"{m.group('right').strip()}**"
            ),
            merged,
        )
        if updated == merged:
            return merged
        merged = updated


def _merge_crossline_bold_hyphens(markdown: str) -> str:
    """Merge bold spans split across lines by hyphenated word breaks.

    pymupdf4llm sometimes breaks a bold run mid-word at a line boundary:
        ``**...Augmented Gen-**``
        ``**eration Systems.**``
    This collapses them into a single bold span with the hyphen resolved.
    Only dehyphenates when the continuation starts with a lowercase letter
    (true word fragment); otherwise preserves the hyphen as intentional.
    """

    def _join(m: re.Match) -> str:
        left = m.group("left").rstrip()
        right = m.group("right")
        if right and right[0].islower():
            return f"**{left}{right}**"
        return f"**{left}-{right}**"

    merged = markdown
    while True:
        updated = _CROSSLINE_HYPHEN_BOLD_RE.sub(_join, merged)
        if updated == merged:
            return merged
        merged = updated


def _promote_bold_only_paragraphs(markdown: str) -> str:
    """Promote residual bold-only lines that behave like structural headings."""
    lines = markdown.splitlines()
    promoted: list[str] = []

    for idx, line in enumerate(lines):
        stripped = line.strip()
        match = _BOLD_ONLY_LINE_RE.fullmatch(stripped)
        if match is None:
            promoted.append(line)
            continue

        candidate = re.sub(r"\s+", " ", match.group("text")).strip()
        if (
            not candidate
            or len(candidate) > _MAX_PROMOTED_HEADING_CHARS
            or _SENTENCE_TERMINAL_RE.search(candidate)
            or _PREFIX_BLOCKLIST.match(candidate)
            or _ALGO_KEYWORD_RE.match(candidate)
        ):
            promoted.append(line)
            continue

        next_non_empty = ""
        for next_line in lines[idx + 1 :]:
            next_non_empty = next_line.strip()
            if next_non_empty:
                break

        if not next_non_empty or _TABLE_OR_IMAGE_LINE_RE.match(next_non_empty):
            promoted.append(line)
            continue

        _LOG.debug(
            "Promoting PDF bold-only line to heading: text=%r signals=%s",
            candidate,
            ["standalone", "length_ok", "no_sentence_terminal", "followed_by_body"],
        )
        promoted.append(f"## {candidate}")

    return "\n".join(promoted)


def normalize_pdf_headings(markdown: str) -> str:
    """Convert pymupdf4llm bold heading patterns to ATX headings.

    Pass 1 merges adjacent inline bold runs produced by pymupdf4llm so split
    headings like ``**A** **Comprehensive** **Taxonomy**`` become a single bold
    span. Pass 2 applies deterministic numbered/known-heading rewrites, then
    heuristically promotes remaining bold-only paragraphs when they behave like
    structural headings rather than captions or inline emphasis.

    Deterministic rewrites cover four dominant patterns:

      ``**3.1** **Problem Formulation**``  →  ``### 3.1 Problem Formulation``
      ``**5.3.2** **GraphRAG** **and** **Community-Based** **Hierarchies**``
                                           →  ``#### 5.3.2 GraphRAG and Community-Based Hierarchies``
      ``**3.1 Problem Formulation**``      →  ``### 3.1 Problem Formulation``
      ``### **Title**``                    →  ``### Title``
      ``**Abstract**``                     →  ``## Abstract``

    Suppresses false positives via prefix blocklist (Table, Figure, Algorithm)
    and algorithm keyword filter (for, if, while, return, …).
    """

    def _replace_numbered(m: re.Match) -> str:
        num = m.group("num").rstrip(".")
        title = re.sub(r"\*\*", "", m.group("title")).strip()
        title = re.sub(r"\s+", " ", title)
        if _PREFIX_BLOCKLIST.match(title) or _ALGO_KEYWORD_RE.match(title):
            return m.group(0)
        depth = _heading_depth(num)
        return "#" * depth + f" {num} {title}"

    def _replace_roman(m: re.Match) -> str:
        num = m.group("num")
        title = m.group("title").strip()
        if _PREFIX_BLOCKLIST.match(title):
            return m.group(0)
        return f"## {num} {title}"

    def _replace_atx_bold(m: re.Match) -> str:
        prefix = m.group(1)
        content = m.group(2)
        num_match = re.match(r"^(\d+(?:\.\d+)*)\s+", content)
        if num_match:
            depth = _heading_depth(num_match.group(1))
            return "#" * depth + f" {content}"
        return f"{prefix} {content}"

    markdown = _merge_adjacent_bold_runs(markdown)
    markdown = _merge_crossline_bold_hyphens(markdown)
    markdown = _NUMBERED_SEPARATE_RE.sub(_replace_numbered, markdown)
    markdown = _NUMBERED_SINGLE_RE.sub(_replace_numbered, markdown)
    markdown = _ROMAN_SEPARATE_RE.sub(_replace_roman, markdown)
    markdown = _UNNUMBERED_ALONE_RE.sub(lambda m: f"## {m.group(1)}", markdown)
    markdown = _ATX_BOLD_RE.sub(_replace_atx_bold, markdown)
    return _promote_bold_only_paragraphs(markdown)


def chunk_pdf(
    path: str,
    *,
    target_chars: int = _CHUNK_CHARS_TARGET,
    pad_chars: int = _CHUNK_CHARS_PAD,
) -> list[Chunk]:
    """Convert PDF to markdown via pymupdf4llm, normalize headings, then chunk.

    Applies ``normalize_pdf_headings`` after running-header removal to convert
    pymupdf4llm bold patterns (``**3.1** **Title**``) into ATX headings so that
    ``parse_sections`` produces hierarchical section paths on every chunk.
    """
    try:
        import pymupdf4llm
    except ImportError as exc:
        raise RuntimeError(
            "Missing PDF extraction dependency. "
            "Install with: pip install pymupdf4llm pymupdf-layout"
        ) from exc

    try:
        page_data = pymupdf4llm.to_markdown(path, page_chunks=True)
    except Exception as e:
        raise RuntimeError(f"Failed to convert PDF '{path}' to markdown: {e}") from e

    if isinstance(page_data, list):
        pages = [
            p["text"] if isinstance(p, dict) and "text" in p else str(p)
            for p in page_data
        ]
    else:
        pages = [str(page_data)]

    pages = _strip_running_headers(pages)
    markdown_text = "\n\n".join(pages)
    markdown_text = normalize_pdf_headings(markdown_text)

    return chunk_markdown(
        path, markdown_text, target_chars=target_chars, pad_chars=pad_chars
    )
