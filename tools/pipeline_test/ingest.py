"""Ingest prompt-engineering PDFs into markdown files for RAG indexing."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import httpx

DEFAULT_OUTPUT_DIR = Path("docs/research/prompting")
DEFAULT_RAG_URL = "http://localhost:8100"
DEFAULT_RAG_TIMEOUT = 30.0


@dataclass(slots=True, kw_only=True)
class IngestRecord:
    source_pdf: Path
    output_md: Path
    converted: bool
    error: str | None = None


type MarkdownResult = str | list[dict[str, object]]
type MarkdownConverter = Callable[[str], MarkdownResult]


def ingest_pdfs(
    *,
    sources: list[str],
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    recursive: bool = True,
) -> list[IngestRecord]:
    """Convert all discovered PDFs to markdown in output_dir."""
    output_dir.mkdir(parents=True, exist_ok=True)
    pdf_files = discover_pdf_files(sources=sources, default_dir=output_dir, recursive=recursive)
    markdown_converter = _load_converter()

    records: list[IngestRecord] = []
    used_paths: set[Path] = set(output_dir.glob("*.md"))
    for pdf_path in pdf_files:
        output_path = _build_output_path(pdf_path=pdf_path, output_dir=output_dir, used_paths=used_paths)
        used_paths.add(output_path)
        try:
            markdown_raw = markdown_converter(str(pdf_path))
            markdown = _normalize_markdown(markdown_raw)
            output_path.write_text(markdown, encoding="utf-8")
            records.append(
                IngestRecord(
                    source_pdf=pdf_path,
                    output_md=output_path,
                    converted=True,
                )
            )
        except Exception as exc:  # noqa: BLE001
            records.append(
                IngestRecord(
                    source_pdf=pdf_path,
                    output_md=output_path,
                    converted=False,
                    error=str(exc),
                )
            )
    return records


def discover_pdf_files(
    *,
    sources: list[str],
    default_dir: Path,
    recursive: bool,
) -> list[Path]:
    """Collect unique PDF files from explicit files or directories."""
    roots = [Path(item) for item in sources] if sources else [default_dir]
    pdf_paths: list[Path] = []
    seen: set[Path] = set()

    for root in roots:
        path = root.expanduser().resolve()
        if path.is_file():
            if path.suffix.lower() == ".pdf" and path not in seen:
                seen.add(path)
                pdf_paths.append(path)
            continue
        if not path.is_dir():
            continue
        candidates = path.rglob("*.pdf") if recursive else path.glob("*.pdf")
        for candidate in candidates:
            resolved = candidate.resolve()
            if resolved not in seen:
                seen.add(resolved)
                pdf_paths.append(resolved)

    pdf_paths.sort()
    return pdf_paths


def index_markdown_directory(
    *,
    directory: Path,
    rag_url: str = DEFAULT_RAG_URL,
    timeout: float = DEFAULT_RAG_TIMEOUT,
) -> tuple[dict[str, Any] | None, str | None]:
    """Ask RAG to index markdown files in a directory."""
    url = f"{rag_url.rstrip('/')}/index_directory"
    body: dict[str, Any] = {
        "path": str(directory.expanduser().resolve()),
        "extensions": [".md"],
    }
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.post(url, json=body)
        _ = response.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)
    return response.json(), None


def rag_watch_status(
    *,
    rag_url: str = DEFAULT_RAG_URL,
    timeout: float = DEFAULT_RAG_TIMEOUT,
) -> tuple[list[dict[str, Any]] | None, str | None]:
    """Return current RAG watch paths for quick diagnostics."""
    url = f"{rag_url.rstrip('/')}/watch/status"
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.get(url)
        _ = response.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)

    payload = response.json()
    if not isinstance(payload, list):
        return None, "Unexpected /watch/status response"
    watches: list[dict[str, Any]] = [
        {str(key): value for key, value in item.items()}
        for item in payload
        if isinstance(item, dict)
    ]
    return watches, None


def _load_converter() -> MarkdownConverter:
    """Load pymupdf4llm converter with actionable install guidance."""
    try:
        import pymupdf4llm
    except ImportError as exc:
        raise RuntimeError(
            "Missing PDF extraction dependencies. "
            "Install with: pip install pymupdf4llm pymupdf-layout"
        ) from exc
    return pymupdf4llm.to_markdown


def _normalize_markdown(markdown: MarkdownResult) -> str:
    """Normalize pymupdf4llm output into markdown text."""
    if isinstance(markdown, str):
        return markdown
    return "\n\n".join(str(item) for item in markdown)


def _build_output_path(pdf_path: Path, output_dir: Path, used_paths: set[Path]) -> Path:
    """Map source PDF to a unique markdown file in output_dir."""
    base = output_dir / f"{pdf_path.stem}.md"
    if base not in used_paths:
        return base

    suffix = pdf_path.parent.name.replace(" ", "-")
    if suffix:
        candidate = output_dir / f"{pdf_path.stem}-{suffix}.md"
        if candidate not in used_paths:
            return candidate

    for index in range(2, 1000):
        candidate = output_dir / f"{pdf_path.stem}-{index}.md"
        if candidate not in used_paths:
            return candidate
    raise RuntimeError(f"Could not find output name for {pdf_path}")
