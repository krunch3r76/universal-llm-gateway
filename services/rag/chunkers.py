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

import re
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
    the heading prefix does not count toward the target/pad budget.
    """

    def _add_chunks_from_section(section_text: str, heading_prefix: str) -> None:
        for text, overlap_len in _split_paragraphs(
            section_text, target_chars, pad_chars, overlap_paragraphs
        ):
            full_text = f"## {heading_prefix}\n\n{text}" if heading_prefix else text
            chunks.append(
                Chunk(
                    text=full_text,
                    metadata={
                        "source": source,
                        "heading": heading_prefix.strip(),
                        "section_path": heading_prefix.strip(),
                        "overlap_prefix_len": overlap_len,
                    },
                )
            )

    chunks: list[Chunk] = []
    source = path
    for sec in parse_sections(content):
        body = content[sec.start : sec.end]
        if not body.strip():
            continue
        if len(body) <= target_chars:
            full_text = f"## {sec.path}\n\n{body.strip()}" if sec.path else body.strip()
            chunks.append(
                Chunk(
                    text=full_text,
                    metadata={
                        "source": source,
                        "heading": sec.path,
                        "section_path": sec.path,
                        "overlap_prefix_len": 0,
                    },
                )
            )
        else:
            _add_chunks_from_section(body, sec.path or sec.heading)

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


def chunk_pdf(
    path: str,
    *,
    target_chars: int = _CHUNK_CHARS_TARGET,
    pad_chars: int = _CHUNK_CHARS_PAD,
) -> list[Chunk]:
    """Convert PDF to markdown via pymupdf4llm, strip running headers, then chunk."""
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

    return chunk_markdown(
        path, markdown_text, target_chars=target_chars, pad_chars=pad_chars
    )


def chunk_epub(
    path: str,
    *,
    target_chars: int = _CHUNK_CHARS_EBOOK,
    pad_chars: int = _CHUNK_CHARS_EBOOK_PAD,
) -> list[Chunk]:
    """Extract EPUB chapters via ebooklib, convert to text, chunk as markdown."""
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
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text(separator="\n\n", strip=True)
        if text:
            sections.append(text)

    if not sections:
        return []

    combined = "\n\n".join(sections)
    return chunk_markdown(
        path, combined, target_chars=target_chars, pad_chars=pad_chars
    )


def normalize_html_to_markdown(path: str, html: str) -> str:
    """Convert HTML into deterministic markdown for chunking/indexing."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.select("script, style, noscript, template, svg, canvas, iframe"):
        tag.decompose()
    for node in soup.select("[hidden], [aria-hidden='true']"):
        node.decompose()
    for node in soup.select(_BOILERPLATE_SELECTORS):
        node.decompose()

    root = soup.select_one("main") or soup.select_one("article") or soup.body or soup
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
    """Convert HTML to markdown, then chunk as markdown."""
    raw_html = Path(path).read_text(errors="replace")
    markdown = normalize_html_to_markdown(path, raw_html)
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

    if suffix in _CODE_EXTENSIONS:
        # Code chunkers use max_chunk_chars; map target_chars to the same budget.
        max_chunk_chars = target_chars
        return chunk_code(
            str(path), path.read_text(errors="replace"), max_chunk_chars=max_chunk_chars
        )

    raise ValueError(f"Unsupported file extension: {suffix!r} for {path}")
