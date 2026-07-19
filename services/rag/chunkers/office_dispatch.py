"""Office formats and extension dispatch."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from services.rag.chunkers._sizing import (
    _CHUNK_CHARS_PAD,
    _CHUNK_CHARS_TARGET,
    _CODE_EXTENSIONS,
    _HTML_EXTENSIONS,
)
from services.rag.chunkers.code_chunking import chunk_code
from services.rag.chunkers.epub_html import chunk_epub, chunk_html
from services.rag.chunkers.markdown_pdf import chunk_markdown, chunk_pdf
from services.rag.chunkers.paragraph_utils import Chunk

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

    chunks = chunk_markdown(
        path, markdown, target_chars=target_chars, pad_chars=pad_chars
    )
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
                [
                    "soffice",
                    "--headless",
                    "--convert-to",
                    "txt",
                    "--outdir",
                    tmpdir,
                    path,
                ],
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
