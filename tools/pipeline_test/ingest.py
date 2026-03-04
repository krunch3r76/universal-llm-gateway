"""Ingest prompt-engineering PDFs into a RAG-watched corpus directory.

PDFs are copied into the corpus directory (the canonical source of truth).
RAG handles PDF-to-markdown conversion internally via pymupdf4llm.
Duplicate detection uses SHA-256 of PDF bytes — both locally (same hash
already in corpus) and server-side (pdf_hash in ChromaDB metadata).
"""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from transport_utils.rag_client import DEFAULT_RAG_URL, make_sync_client

DEFAULT_CORPUS_DIR = Path("docs/research/prompting")
DEFAULT_RAG_TIMEOUT = 30.0


@dataclass(slots=True, kw_only=True)
class IngestRecord:
    source_pdf: Path
    archived_to: Path | None = None
    skipped_duplicate: bool = False
    duplicate_of: Path | None = None
    error: str | None = None


def ingest_pdfs(
    *,
    sources: list[str],
    corpus_dir: Path = DEFAULT_CORPUS_DIR,
    recursive: bool = True,
) -> list[IngestRecord]:
    """Copy discovered PDFs into corpus_dir, skipping local hash duplicates."""
    corpus_dir.mkdir(parents=True, exist_ok=True)

    existing_hashes = _scan_corpus_hashes(corpus_dir)
    pdf_files = discover_pdf_files(
        sources=sources,
        default_dir=corpus_dir,
        recursive=recursive,
    )

    records: list[IngestRecord] = []
    for pdf_path in pdf_files:
        pdf_hash = _hash_file(pdf_path)

        if pdf_hash in existing_hashes:
            records.append(
                IngestRecord(
                    source_pdf=pdf_path,
                    skipped_duplicate=True,
                    duplicate_of=existing_hashes[pdf_hash],
                )
            )
            continue

        try:
            dest = _copy_to_corpus(pdf_path, corpus_dir)
            existing_hashes[pdf_hash] = dest
            records.append(IngestRecord(source_pdf=pdf_path, archived_to=dest))
        except Exception as exc:  # noqa: BLE001
            records.append(IngestRecord(source_pdf=pdf_path, error=str(exc)))

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


def index_corpus_directory(
    *,
    directory: Path,
    rag_url: str = DEFAULT_RAG_URL,
    timeout: float = DEFAULT_RAG_TIMEOUT,
    metadata_overrides: dict[str, str | int | float | bool] | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Ask RAG to index PDF files in a directory."""
    body: dict[str, Any] = {
        "path": str(directory.expanduser().resolve()),
        "extensions": [".pdf"],
    }
    if metadata_overrides:
        body["metadata_overrides"] = metadata_overrides
    try:
        with make_sync_client(rag_url, timeout=timeout) as client:
            response = client.post("/index_directory", json=body)
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
    try:
        with make_sync_client(rag_url, timeout=timeout) as client:
            response = client.get("/watch/status")
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


def _hash_file(path: Path) -> str:
    """SHA-256 hex digest of file contents."""
    hasher = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _scan_corpus_hashes(corpus_dir: Path) -> dict[str, Path]:
    """Map content hash → path for all PDFs already in the corpus directory."""
    hashes: dict[str, Path] = {}
    for pdf_path in corpus_dir.glob("*.pdf"):
        hashes[_hash_file(pdf_path)] = pdf_path
    return hashes


def _copy_to_corpus(pdf_path: Path, corpus_dir: Path) -> Path:
    """Copy PDF into corpus directory with collision avoidance."""
    dest = corpus_dir / pdf_path.name
    if dest.exists() and _hash_file(dest) == _hash_file(pdf_path):
        return dest
    counter = 2
    while dest.exists():
        dest = corpus_dir / f"{pdf_path.stem}-{counter}{pdf_path.suffix}"
        counter += 1
    shutil.copy2(pdf_path, dest)
    return dest
