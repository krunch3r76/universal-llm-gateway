"""Document chunking for RAG indexing.

Splits files into chunks suitable for embedding and LLM-based knowledge extraction.
Two chunking strategies are implemented — non-code and code — with distinct goals:

  Non-code (markdown, PDF, EPUB):
    Uses adaptive target+pad sizing rather than a hard maximum.  A chunk grows
    until it reaches ``target_chars``; the pad zone (``pad_chars``) allows it to
    absorb an additional partial paragraph rather than orphaning it.  Two-paragraph
    overlap between adjacent chunks prevents concept fragmentation at boundaries.
    Each chunk is prefixed with its nearest parent heading so extraction models
    have section context even when the heading fell in the previous chunk.

    The larger target (≈1024 tokens / 4096 chars) is sized to match the context
    window of the qwen3-embedding-8b-q8-0 model and to give the extraction LLM
    enough surrounding text to reliably identify entities, relations, and topics.

  Code (Python via tree-sitter):
    Splits at AST node boundaries (functions, classes) to preserve syntactic
    completeness.  Smaller target (≈256 tokens) keeps embedding granularity tight.
    AST metadata (complexity, node type, symbol name) is stored in chunk metadata
    for future code-specific retrieval enhancements.

Chunk IDs are deterministic: ``{content_hash_prefix}-{i}`` where the hash
incorporates file bytes and the extraction schema version.  A schema version bump
invalidates all existing hashes, forcing a full re-index without manual intervention.

Entry point: ``chunk_file(path, target_chars=None)`` dispatches by file extension.
"""

import logging
import re
import subprocess
import tempfile
from collections import Counter
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

import tree_sitter as _ts
import tree_sitter_python as _tspython
from bs4 import BeautifulSoup
from markdown_sections import parse_sections
from markdownify import markdownify as md

from services.rag.chunker_ast_metadata import (
    build_python_chunk_metadata,
)

_TOKEN_ESTIMATE = 4  # chars per token approximation

_CHUNK_TOKENS_TARGET = 1024
_CHUNK_TOKENS_PAD = 256
_CHUNK_TOKENS_CODE = 256
_CHUNK_TOKENS_EBOOK = 1024
_CHUNK_TOKENS_EBOOK_PAD = 256

_CHUNK_CHARS_TARGET = _CHUNK_TOKENS_TARGET * _TOKEN_ESTIMATE  # 4096
_CHUNK_CHARS_PAD = _CHUNK_TOKENS_PAD * _TOKEN_ESTIMATE  # 1024
_CHUNK_CHARS_CODE = _CHUNK_TOKENS_CODE * _TOKEN_ESTIMATE
_CHUNK_CHARS_EBOOK = _CHUNK_TOKENS_EBOOK * _TOKEN_ESTIMATE  # 4096
_CHUNK_CHARS_EBOOK_PAD = _CHUNK_TOKENS_EBOOK_PAD * _TOKEN_ESTIMATE  # 1024

_CODE_EXTENSIONS = {".py", ".js", ".ts", ".go", ".rs", ".sh", ".yaml", ".toml"}

_HTML_EXTENSIONS = {".html", ".htm"}
_BOILERPLATE_SELECTORS = (
    "nav, header, footer, aside, [role='navigation'], [aria-label*='cookie' i], "
    "[class*='cookie' i], [id*='cookie' i], [class*='consent' i], [id*='consent' i], "
    "[class*='banner' i], [id*='banner' i], [class*='sidebar' i], [id*='sidebar' i], "
    "[class*='advert' i], [id*='advert' i], [class*='ad-' i], [id*='ad-']"
)

_PY_LANG = _ts.Language(_tspython.language())
_PY_PARSER = _ts.Parser(_PY_LANG)
_AST_CHUNK_NWS_CHARS = _CHUNK_CHARS_CODE
_LOG = logging.getLogger(__name__)


@dataclass(slots=True, kw_only=True)
class Chunk:
    text: str
    metadata: dict[str, str | int | float | bool]


def _word_split(text: str, max_chars: int) -> list[str]:
    """Split text at word boundaries up to max_chars per piece."""
    words = text.split()
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for word in words:
        if current_len + len(word) + 1 > max_chars and current:
            chunks.append(" ".join(current))
            current = []
            current_len = 0
        current.append(word)
        current_len += len(word) + 1
    if current:
        chunks.append(" ".join(current))
    # Hard-truncation last resort (no whitespace at all).
    if not chunks and text:
        return [text[i : i + max_chars] for i in range(0, len(text), max_chars)]
    return chunks


def _split_oversized(para: str, max_chars: int) -> list[str]:
    """Split a single paragraph that individually exceeds max_chars.

    Strategy (cascade):
    1. Table rows: split at \\n boundaries for pipe-table content,
       recursing into _word_split for any row that still exceeds max_chars.
    2. Word-boundary split for prose.
    3. Hard-truncation as final fallback.
    """
    if "|" in para and "\n" in para:
        rows = para.split("\n")
        sub_chunks: list[str] = []
        current_rows: list[str] = []
        current_len = 0
        for row in rows:
            if current_len + len(row) + 1 > max_chars and current_rows:
                sub_chunks.append("\n".join(current_rows))
                current_rows = []
                current_len = 0
            if len(row) > max_chars:
                # Single row too large — word-split it, flush first.
                # _word_split may hard-truncate a single word; table formatting can break.
                if current_rows:
                    sub_chunks.append("\n".join(current_rows))
                    current_rows = []
                    current_len = 0
                sub_chunks.extend(_word_split(row, max_chars))
            else:
                current_rows.append(row)
                current_len += len(row) + 1
        if current_rows:
            sub_chunks.append("\n".join(current_rows))
        return sub_chunks

    return _word_split(para, max_chars)


def _overlap_prefix_len(carry: list[str]) -> int:
    """Char count of carry paragraphs joined with '\\n\\n' (no trailing separator)."""
    if not carry:
        return 0
    return sum(len(p) for p in carry) + max(0, (len(carry) - 1) * 2)


def _paras_len(paras: list[str]) -> int:
    return sum(len(p) for p in paras) + max(0, (len(paras) - 1) * 2)


def _char_upto_line(lines: list[str], line_idx: int) -> int:
    """Char offset at the start of 0-indexed ``line_idx``."""
    if line_idx <= 0:
        return 0
    safe_idx = min(line_idx, len(lines))
    return sum(len(lines[k]) for k in range(safe_idx))


def _split_paragraphs(
    text: str,
    target_chars: int,
    pad_chars: int,
    overlap_paragraphs: int = 2,
) -> list[tuple[str, int]]:
    """Split text into chunks with soft target, pad zone, and paragraph overlap.

    Returns (chunk_text, overlap_prefix_len) pairs.
    - Below target: keep accumulating paragraphs
    - In pad zone (target..target+pad): emit at next paragraph boundary
    - Above target+pad: force split via _split_oversized (overlap_prefix_len=0)

    ∀ chunk after first: starts with last `overlap_paragraphs` paragraphs from
    the previous chunk. overlap_prefix_len = char count of that carried-forward
    text (0 for first chunk and oversized fallback chunks).
    """
    hard_max = target_chars + pad_chars
    paragraphs = re.split(r"\n{2,}", text.strip())
    results: list[tuple[str, int]] = []
    current: list[str] = []
    current_len = 0
    carry: list[str] = []  # overlap paragraphs carried from the previous chunk

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        if len(para) > hard_max:
            if current:
                results.append(("\n\n".join(current), _overlap_prefix_len(carry)))
            for piece in _split_oversized(para, hard_max):
                results.append((piece, 0))
            current = []
            current_len = 0
            carry = []
            continue

        new_len = current_len + (2 if current else 0) + len(para)

        if new_len > target_chars and current:
            if new_len <= hard_max:
                # In pad zone — include this paragraph, then emit.
                current.append(para)
                results.append(("\n\n".join(current), _overlap_prefix_len(carry)))
                carry = (
                    current[-overlap_paragraphs:]
                    if len(current) > overlap_paragraphs
                    else list(current)
                )
                current = list(carry)
                current_len = _paras_len(current)
            else:
                # Past pad zone — emit without this paragraph, then start new window.
                results.append(("\n\n".join(current), _overlap_prefix_len(carry)))
                carry = (
                    current[-overlap_paragraphs:]
                    if len(current) > overlap_paragraphs
                    else list(current)
                )
                current = list(carry) + [para]
                current_len = _paras_len(current)
        else:
            current.append(para)
            current_len = new_len

    if current:
        results.append(("\n\n".join(current), _overlap_prefix_len(carry)))

    return results


def _annotate_chunk_indices(chunks: list[Chunk]) -> list[Chunk]:
    for index, chunk in enumerate(chunks):
        chunk.metadata["chunk_index"] = index
    return chunks


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


def _normalize_epub_chapter_html(path: str, html_bytes: bytes) -> str:
    """Convert a single EPUB chapter's HTML to markdown with heading structure.

    Uses the same ``normalize_html_to_markdown`` pipeline as ``.html`` files so
    that ``<h1>``–``<h6>`` tags become ATX headings and ``parse_sections`` in
    ``chunk_markdown`` can produce hierarchical section paths.

    Falls back to plain-text extraction when markdownify produces empty output
    (e.g. image-only chapters).
    """
    raw_html = html_bytes.decode("utf-8", errors="replace")
    try:
        return normalize_html_to_markdown(path, raw_html)
    except ValueError:
        soup = BeautifulSoup(raw_html, "html.parser")
        return soup.get_text(separator="\n\n", strip=True)


def chunk_epub(
    path: str,
    *,
    target_chars: int = _CHUNK_CHARS_EBOOK,
    pad_chars: int = _CHUNK_CHARS_EBOOK_PAD,
) -> list[Chunk]:
    """Extract EPUB chapters via ebooklib, normalize to markdown, then chunk.

    Each chapter's HTML is converted to markdown through the same
    ``normalize_html_to_markdown`` pipeline used by ``.html`` files, preserving
    heading structure (``<h1>``–``<h6>`` → ATX headings).  The combined markdown
    is then chunked via ``chunk_markdown`` so that ``parse_sections`` produces
    hierarchical ``section_path`` metadata on every chunk — identical to the PDF
    and HTML ingestion paths.
    """
    try:
        import ebooklib
        from ebooklib import epub
    except ImportError as exc:
        raise RuntimeError(
            "Missing EPUB extraction dependency. "
            "Install with: pip install ebooklib beautifulsoup4"
        ) from exc
    try:
        book = epub.read_epub(path, options={"ignore_ncx": True})
    except Exception as e:
        raise RuntimeError(f"Failed to read EPUB '{path}': {e}") from e

    sections: list[str] = []
    for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
        html = item.get_body_content()
        if not html:
            continue
        markdown = _normalize_epub_chapter_html(path, html)
        if markdown:
            sections.append(markdown)

    if not sections:
        return []

    combined = "\n\n".join(sections)
    combined = _ATX_BOLD_RE.sub(lambda m: f"{m.group(1)} {m.group(2)}", combined)
    combined = _promote_bold_only_paragraphs(combined)
    chunks = chunk_markdown(
        path, combined, target_chars=target_chars, pad_chars=pad_chars
    )
    for chunk in chunks:
        chunk.metadata["source_format"] = "epub"
        chunk.metadata["normalized_format"] = "markdown"
    return chunks


def normalize_html_to_markdown(path: str, html: str) -> str:
    """Convert HTML into deterministic markdown for chunking/indexing."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.select("script, style, noscript, template, svg, canvas, iframe"):
        tag.decompose()
    for node in soup.select("[hidden], [aria-hidden='true']"):
        node.decompose()

    root = soup.select_one("main") or soup.select_one("article") or soup.body or soup

    # Guard: never remove a boilerplate candidate that holds the majority of the
    # root text — substring class selectors like [class*='sidebar'] can false-positive
    # on content wrappers (e.g. class="parade-loop-sidebar" containing all content).
    root_text_len = len(root.get_text())
    for node in root.select(_BOILERPLATE_SELECTORS):
        if node is root:
            continue
        if root_text_len > 0 and len(node.get_text()) > root_text_len * 0.5:
            continue
        node.decompose()
    markdown = md(
        str(root),
        heading_style="ATX",
        bullets="-",
        strip=["span", "font"],
        escape_asterisks=False,
        escape_underscores=False,
    )
    markdown = "\n".join(line.rstrip() for line in markdown.splitlines())
    while "\n\n\n" in markdown:
        markdown = markdown.replace("\n\n\n", "\n\n")
    cleaned = markdown.strip()
    if not cleaned:
        raise ValueError(f"HTML normalization produced empty markdown for {path}")
    return cleaned


def chunk_html(
    path: str,
    *,
    target_chars: int = _CHUNK_CHARS_TARGET,
    pad_chars: int = _CHUNK_CHARS_PAD,
) -> list[Chunk]:
    """Convert HTML to markdown, then chunk as markdown.

    JS-rendered shells (e.g. Scribd, SPAs) produce empty markdown after
    stripping scripts and boilerplate. These are skipped with a warning
    rather than propagating ValueError, so the indexer can continue past
    un-extractable files.
    """
    raw_html = Path(path).read_text(errors="replace")
    try:
        markdown = normalize_html_to_markdown(path, raw_html)
    except ValueError:
        _LOG.warning(
            "Skipping %s — HTML normalization produced no extractable text"
            " (likely a JS-rendered shell with no static document content)",
            path,
        )
        return []
    chunks = chunk_markdown(
        path, markdown, target_chars=target_chars, pad_chars=pad_chars
    )
    for chunk in chunks:
        chunk.metadata["source_format"] = "html"
        chunk.metadata["normalized_format"] = "markdown"
    return chunks


# ---------------------------------------------------------------------------
# AST-aware code chunking (tree-sitter, cAST algorithm)
# ---------------------------------------------------------------------------


def _nws_len(text: bytes) -> int:
    """Non-whitespace byte count (cAST chunk-size metric)."""
    return (
        len(text)
        - text.count(b" ")
        - text.count(b"\t")
        - text.count(b"\n")
        - text.count(b"\r")
    )


def _node_nws(source: bytes, node: _ts.Node) -> int:
    return _nws_len(source[node.start_byte : node.end_byte])


def _node_identifier(source: bytes, node: _ts.Node) -> str | None:
    """Extract identifier name from a function/class/decorated definition."""
    target = node
    if node.type == "decorated_definition":
        for child in node.children:
            if child.type in ("function_definition", "class_definition"):
                target = child
                break
        else:
            return None
    for child in target.children:
        if child.type == "identifier":
            return source[child.start_byte : child.end_byte].decode("utf-8")
    return None


def _class_scope(source: bytes, node: _ts.Node) -> str | None:
    """Return class name if this node introduces a class scope for its children."""
    if node.type == "class_definition":
        return _node_identifier(source, node)
    if node.type == "decorated_definition":
        for child in node.children:
            if child.type == "class_definition":
                return _node_identifier(source, child)
    return None


def _split_children(node: _ts.Node) -> list[_ts.Node]:
    """Choose semantic children when splitting oversized nodes."""
    if node.type in {"class_definition", "function_definition"}:
        body = node.child_by_field_name("body")
        if body is not None and body.children:
            return list(body.children)
    return list(node.children)


def _chunk_ast_nodes(
    nodes: list[_ts.Node],
    source: bytes,
    max_nws: int,
    class_name: str | None,
) -> list[tuple[list[_ts.Node], str | None]]:
    """cAST Algorithm 1: recursive split-merge on AST sibling nodes.

    Returns (node_list, class_name) pairs — each pair becomes one Chunk.
    """
    results: list[tuple[list[_ts.Node], str | None]] = []
    current: list[_ts.Node] = []
    current_size = 0

    for node in nodes:
        s = _node_nws(source, node)

        if current_size + s > max_nws:
            if current:
                results.append((current, class_name))
                current = []
                current_size = 0

            if s > max_nws:
                # Keep decorated definitions atomic so decorators are never detached.
                if node.type == "decorated_definition":
                    results.append(([node], class_name))
                    continue

                children = _split_children(node)
                if children:
                    child_class = _class_scope(source, node) or class_name
                    results.extend(
                        _chunk_ast_nodes(children, source, max_nws, child_class)
                    )
                else:
                    results.append(([node], class_name))
            else:
                current = [node]
                current_size = s
        else:
            current.append(node)
            current_size += s

    if current:
        results.append((current, class_name))

    return results


def chunk_code_ast(
    path: str,
    content: str,
    max_chunk_chars: int = _AST_CHUNK_NWS_CHARS,
) -> list[Chunk]:
    """AST-aware Python chunker using tree-sitter (cAST split-merge algorithm).

    Chunks align with function/class boundaries.  Size metric is non-whitespace
    character count per the cAST paper (optimal range: 2000–2500).
    """
    source = content.encode()
    tree = _PY_PARSER.parse(source)
    root = tree.root_node

    if _node_nws(source, root) <= max_chunk_chars:
        whole_text = content if content.strip() else ""
        if not whole_text:
            return []
        # Keep metadata generation consistent with multi-chunk path.
        meta = build_python_chunk_metadata(
            path=path,
            source=source,
            text=content,
            nodes=root.children,
            class_scope=None,
            nws_len=_nws_len(content.encode()),
        )
        return _annotate_chunk_indices([Chunk(text=content, metadata=meta)])

    raw = _chunk_ast_nodes(root.children, source, max_chunk_chars, None)
    chunks: list[Chunk] = []
    for nodes, ctx_class in raw:
        if not nodes:
            continue
        text = source[nodes[0].start_byte : nodes[-1].end_byte].decode(
            "utf-8", errors="replace"
        )
        if not text.strip():
            continue

        meta = build_python_chunk_metadata(
            path=path,
            source=source,
            text=text,
            nodes=nodes,
            class_scope=ctx_class,
            nws_len=_nws_len(text.encode()),
        )
        chunks.append(Chunk(text=text, metadata=meta))

    return _annotate_chunk_indices(chunks)


def chunk_code(
    path: str,
    content: str,
    max_chunk_chars: int | None = None,
) -> list[Chunk]:
    """Code chunker: AST-aware for Python, line-based fallback for others."""
    if Path(path).suffix.lower() == ".py":
        budget = max_chunk_chars if max_chunk_chars else _AST_CHUNK_NWS_CHARS
        return chunk_code_ast(path, content, max_chunk_chars=budget)

    suffix = Path(path).suffix.lstrip(".")
    language = suffix or "text"
    source = str(path)
    budget = max_chunk_chars or _CHUNK_CHARS_CODE
    chunks: list[Chunk] = []

    lines = content.splitlines()
    current: list[str] = []
    current_chars = 0

    def _append_current_chunk() -> None:
        nonlocal current, current_chars
        chunk_text = "\n".join(current)
        chunks.append(
            Chunk(
                text=chunk_text,
                metadata={
                    "source": source,
                    "language": language,
                    "chunk_type": "statement_block",
                    "chunk_size_nws_chars": _nws_len(chunk_text.encode()),
                    "is_semantically_complete": False,
                    "chunk_hash": sha256(chunk_text.encode()).hexdigest()[:16],
                },
            )
        )
        current = []
        current_chars = 0

    for line in lines:
        current.append(line)
        current_chars += len(line)
        if current_chars >= budget:
            _append_current_chunk()

    if current:
        _append_current_chunk()

    return _annotate_chunk_indices(chunks)


_DOCX_HEADING_PREFIX: dict[str, str] = {
    "Title": "# ",
    "Subtitle": "## ",
    "Heading 1": "# ",
    "Heading 2": "## ",
    "Heading 3": "### ",
    "Heading 4": "#### ",
    "Heading 5": "##### ",
}


def chunk_docx(
    path: str,
    *,
    target_chars: int = _CHUNK_CHARS_TARGET,
    pad_chars: int = _CHUNK_CHARS_PAD,
) -> list[Chunk]:
    """Extract text from a .docx file and chunk as markdown.

    Maps python-docx paragraph styles to ATX heading prefixes so document
    structure is preserved through the standard markdown chunking pipeline.
    """
    try:
        import docx as _docx
    except ImportError as exc:
        raise RuntimeError(
            "python-docx is required for .docx ingestion: pip install python-docx"
        ) from exc

    doc = _docx.Document(path)
    parts: list[str] = []
    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        prefix = _DOCX_HEADING_PREFIX.get(para.style.name, "")
        parts.append(f"{prefix}{text}")

    markdown = "\n\n".join(parts)
    if not markdown.strip():
        raise ValueError(f"No extractable text in {path}")

    chunks = chunk_markdown(path, markdown, target_chars=target_chars, pad_chars=pad_chars)
    for chunk in chunks:
        chunk.metadata["source_format"] = "docx"
        chunk.metadata["normalized_format"] = "markdown"
    return chunks


def chunk_doc(
    path: str,
    *,
    target_chars: int = _CHUNK_CHARS_TARGET,
    pad_chars: int = _CHUNK_CHARS_PAD,
) -> list[Chunk]:
    """Convert a legacy .doc file to plain text via LibreOffice headless, then chunk.

    Requires ``soffice`` on PATH (LibreOffice). Falls back gracefully with a
    clear error if unavailable so the index run fails fast rather than silently.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            result = subprocess.run(
                ["soffice", "--headless", "--convert-to", "txt", "--outdir", tmpdir, path],
                capture_output=True,
                text=True,
                timeout=120,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                "LibreOffice (soffice) is required for .doc ingestion but was not found on PATH"
            ) from exc

        if result.returncode != 0:
            raise RuntimeError(
                f"LibreOffice conversion failed for {path}: {result.stderr.strip()}"
            )

        txt_path = Path(tmpdir) / (Path(path).stem + ".txt")
        if not txt_path.exists():
            raise RuntimeError(
                f"LibreOffice did not produce output for {path}; stderr: {result.stderr.strip()}"
            )

        text = txt_path.read_text(errors="replace")

    if not text.strip():
        raise ValueError(f"No extractable text in {path}")

    chunks = chunk_markdown(path, text, target_chars=target_chars, pad_chars=pad_chars)
    for chunk in chunks:
        chunk.metadata["source_format"] = "doc"
        chunk.metadata["normalized_format"] = "text"
    return chunks


def chunk_file(
    path: Path,
    *,
    target_chars: int | None = None,
    pad_chars: int | None = None,
) -> list[Chunk]:
    """Dispatch to the correct chunker based on file extension."""
    suffix = path.suffix.lower()
    kwargs: dict[str, int] = {
        k: v
        for k, v in [("target_chars", target_chars), ("pad_chars", pad_chars)]
        if v is not None
    }

    if suffix in {".md", ".mdc", ".txt"}:
        return chunk_markdown(str(path), path.read_text(errors="replace"), **kwargs)

    if suffix == ".pdf":
        return chunk_pdf(str(path), **kwargs)

    if suffix == ".epub":
        return chunk_epub(str(path), **kwargs)

    if suffix in _HTML_EXTENSIONS:
        return chunk_html(str(path), **kwargs)

    if suffix == ".docx":
        return chunk_docx(str(path), **kwargs)

    if suffix == ".doc":
        return chunk_doc(str(path), **kwargs)

    if suffix in _CODE_EXTENSIONS:
        # Code chunkers use max_chunk_chars; map target_chars to the same budget.
        max_chunk_chars = target_chars
        return chunk_code(
            str(path), path.read_text(errors="replace"), max_chunk_chars=max_chunk_chars
        )

    raise ValueError(f"Unsupported file extension: {suffix!r} for {path}")
