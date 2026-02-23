import re
from dataclasses import dataclass
from pathlib import Path

import pypdf

_TOKEN_ESTIMATE = 4  # chars per token approximation

_CHUNK_TOKENS_LARGE = 512
_CHUNK_TOKENS_CODE = 256

_CHUNK_CHARS_LARGE = _CHUNK_TOKENS_LARGE * _TOKEN_ESTIMATE
_CHUNK_CHARS_CODE = _CHUNK_TOKENS_CODE * _TOKEN_ESTIMATE

_CODE_EXTENSIONS = {".py", ".js", ".ts", ".go", ".rs", ".sh", ".yaml", ".toml"}

_HEADER_RE = re.compile(r"^#{1,3} .+", re.MULTILINE)


@dataclass(slots=True, kw_only=True)
class Chunk:
    text: str
    metadata: dict[str, str]


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


def chunk_markdown(path: str, content: str) -> list[Chunk]:
    """Split markdown by headers, then paragraph-split within each section."""
    chunks: list[Chunk] = []
    source = str(path)

    sections = _HEADER_RE.split(content)
    headers = _HEADER_RE.findall(content)

    # Text before the first header
    if sections[0].strip():
        for text in _split_paragraphs(sections[0], _CHUNK_CHARS_LARGE):
            chunks.append(Chunk(text=text, metadata={"source": source, "heading": ""}))

    for header, section_body in zip(headers, sections[1:], strict=False):
        heading = header.lstrip("#").strip()
        for text in _split_paragraphs(section_body, _CHUNK_CHARS_LARGE):
            chunks.append(
                Chunk(text=text, metadata={"source": source, "heading": heading})
            )

    return chunks


def chunk_pdf(path: str, content: bytes) -> list[Chunk]:
    """Extract pages from PDF and paragraph-split each page."""
    import io

    chunks: list[Chunk] = []
    source = str(path)

    reader = pypdf.PdfReader(io.BytesIO(content))
    for page_num, page in enumerate(reader.pages, start=1):
        page_text = page.extract_text() or ""
        for text in _split_paragraphs(page_text, _CHUNK_CHARS_LARGE):
            chunks.append(
                Chunk(text=text, metadata={"source": source, "page": str(page_num)})
            )

    return chunks


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

    return chunks


def chunk_file(path: Path) -> list[Chunk]:
    """Dispatch to the correct chunker based on file extension."""
    suffix = path.suffix.lower()

    if suffix in {".md", ".txt"}:
        return chunk_markdown(str(path), path.read_text(errors="replace"))

    if suffix == ".pdf":
        return chunk_pdf(str(path), path.read_bytes())

    if suffix in _CODE_EXTENSIONS:
        return chunk_code(str(path), path.read_text(errors="replace"))

    raise ValueError(f"Unsupported file extension: {suffix!r} for {path}")
