"""EPUB and HTML chunking."""

from __future__ import annotations

from pathlib import Path

from bs4 import BeautifulSoup
from markdownify import markdownify as md

from services.rag.chunkers._sizing import (
    _CHUNK_CHARS_EBOOK,
    _CHUNK_CHARS_EBOOK_PAD,
    _CHUNK_CHARS_PAD,
    _CHUNK_CHARS_TARGET,
    _GUARDED_BOILERPLATE_SELECTORS,
    _LOG,
    _STRICT_BOILERPLATE_SELECTORS,
)
from services.rag.chunkers.markdown_pdf import (
    _ATX_BOLD_RE,
    _promote_bold_only_paragraphs,
    chunk_markdown,
)
from services.rag.chunkers.paragraph_utils import (
    Chunk,
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

    for node in root.select(_STRICT_BOILERPLATE_SELECTORS):
        if node is root:
            continue
        node.decompose()

    # Guard: never remove a guarded candidate that holds the majority of the
    # root text — substring class selectors like [class*='sidebar'] can false-positive
    # on content wrappers (e.g. class="parade-loop-sidebar" containing all content).
    root_text_len = len(root.get_text())
    for node in root.select(_GUARDED_BOILERPLATE_SELECTORS):
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
