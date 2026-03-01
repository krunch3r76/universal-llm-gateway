import re
from dataclasses import dataclass
from pathlib import Path

_TOKEN_ESTIMATE = 4  # chars per token approximation

_CHUNK_TOKENS_LARGE = 512
_CHUNK_TOKENS_CODE = 256
_CHUNK_TOKENS_EBOOK = 1024

_CHUNK_CHARS_LARGE = _CHUNK_TOKENS_LARGE * _TOKEN_ESTIMATE
_CHUNK_CHARS_CODE = _CHUNK_TOKENS_CODE * _TOKEN_ESTIMATE
_CHUNK_CHARS_EBOOK = _CHUNK_TOKENS_EBOOK * _TOKEN_ESTIMATE

_CODE_EXTENSIONS = {".py", ".js", ".ts", ".go", ".rs", ".sh", ".yaml", ".toml"}

_HEADER_RE: re.Pattern[str] = re.compile(r"^#{1,3} .+", re.MULTILINE)


@dataclass(slots=True, kw_only=True)
class Chunk:
    text: str
    metadata: dict[str, str | int | float | bool]


def _split_paragraphs(text: str, max_chars: int) -> list[str]:
    """Split text into chunks at paragraph boundaries, capped at max_chars."""
    paragraphs = re.split(r"\n{2,}", text.strip())
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        if current_len + len(para) > max_chars and current:
            chunks.append("\n\n".join(current))
            current = []
            current_len = 0
        current.append(para)
        current_len += len(para)

    if current:
        chunks.append("\n\n".join(current))

    return chunks


def _annotate_chunk_indices(chunks: list[Chunk]) -> list[Chunk]:
    for index, chunk in enumerate(chunks):
        chunk.metadata["chunk_index"] = index
    return chunks


def chunk_markdown(
    path: str,
    content: str,
    *,
    max_chunk_chars: int = _CHUNK_CHARS_LARGE,
) -> list[Chunk]:
    """Split markdown by headers, then paragraph-split within each section."""
    chunks: list[Chunk] = []
    source = str(path)

    sections = _HEADER_RE.split(content)
    headers = _HEADER_RE.findall(content)

    if sections[0].strip():
        for text in _split_paragraphs(sections[0], max_chunk_chars):
            chunks.append(Chunk(text=text, metadata={"source": source, "heading": ""}))

    for header, section_body in zip(headers, sections[1:], strict=False):
        heading = header.lstrip("#").strip()
        for text in _split_paragraphs(section_body, max_chunk_chars):
            chunks.append(
                Chunk(text=text, metadata={"source": source, "heading": heading})
            )

    return _annotate_chunk_indices(chunks)


def chunk_pdf(
    path: str,
    *,
    max_chunk_chars: int = _CHUNK_CHARS_LARGE,
) -> list[Chunk]:
    """Convert PDF to markdown via pymupdf4llm, then chunk as markdown."""
    try:
        import pymupdf4llm
    except ImportError as exc:
        raise RuntimeError(
            "Missing PDF extraction dependency. "
            "Install with: pip install pymupdf4llm pymupdf-layout"
        ) from exc

    markdown_text = pymupdf4llm.to_markdown(path)
    if isinstance(markdown_text, list):
        markdown_text = "\n\n".join(str(item) for item in markdown_text)

    return chunk_markdown(path, markdown_text, max_chunk_chars=max_chunk_chars)


def chunk_epub(
    path: str,
    *,
    max_chunk_chars: int = _CHUNK_CHARS_EBOOK,
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
        from bs4 import BeautifulSoup
    except ImportError as exc:
        raise RuntimeError(
            "Missing HTML parsing dependency. Install with: pip install beautifulsoup4"
        ) from exc

    book = epub.read_epub(path, options={"ignore_ncx": True})
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
    return chunk_markdown(path, combined, max_chunk_chars=max_chunk_chars)


def chunk_code(path: str, content: str) -> list[Chunk]:
    """Line-based code chunker at ~256 tokens per chunk."""
    suffix = Path(path).suffix.lstrip(".")
    language = suffix or "text"
    source = str(path)
    chunks: list[Chunk] = []

    lines = content.splitlines()
    current: list[str] = []
    current_chars = 0

    for line in lines:
        current.append(line)
        current_chars += len(line)
        if current_chars >= _CHUNK_CHARS_CODE:
            chunks.append(
                Chunk(
                    text="\n".join(current),
                    metadata={"source": source, "language": language},
                )
            )
            current = []
            current_chars = 0

    if current:
        chunks.append(
            Chunk(
                text="\n".join(current),
                metadata={"source": source, "language": language},
            )
        )

    return _annotate_chunk_indices(chunks)


def chunk_file(
    path: Path,
    *,
    max_chunk_chars: int | None = None,
) -> list[Chunk]:
    """Dispatch to the correct chunker based on file extension."""
    suffix = path.suffix.lower()

    if suffix in {".md", ".mdc", ".txt"}:
        kwargs = {"max_chunk_chars": max_chunk_chars} if max_chunk_chars else {}
        return chunk_markdown(str(path), path.read_text(errors="replace"), **kwargs)

    if suffix == ".pdf":
        kwargs = {"max_chunk_chars": max_chunk_chars} if max_chunk_chars else {}
        return chunk_pdf(str(path), **kwargs)

    if suffix == ".epub":
        kwargs = {"max_chunk_chars": max_chunk_chars} if max_chunk_chars else {}
        return chunk_epub(str(path), **kwargs)

    if suffix in _CODE_EXTENSIONS:
        return chunk_code(str(path), path.read_text(errors="replace"))

    raise ValueError(f"Unsupported file extension: {suffix!r} for {path}")
