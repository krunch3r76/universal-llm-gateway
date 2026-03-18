"""Ingest prompt-engineering PDFs into a RAG-watched corpus directory.

PDFs are copied into the corpus directory (the canonical source of truth).
RAG handles PDF-to-markdown conversion internally via pymupdf4llm.
Duplicate detection uses SHA-256 of PDF bytes — both locally (same hash
already in corpus) and server-side (pdf_hash in ChromaDB metadata).
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import yaml
from transport_utils.rag_client import DEFAULT_RAG_URL, make_sync_client

logger = logging.getLogger(__name__)

DEFAULT_CORPUS_DIR = Path("docs/research/prompting")
DEFAULT_RAG_TIMEOUT = 30.0
DEFAULT_REGISTRY_PATH = Path.home() / ".rag" / "article_registry.yaml"


@dataclass(slots=True, kw_only=True)
class IngestRecord:
    """Outcome of an attempt to ingest a single PDF (archived, skipped duplicate, or error)."""

    source_pdf: Path
    archived_to: Path | None = None
    skipped_duplicate: bool = False
    duplicate_of: Path | None = None
    error: str | None = None
    content_hash: str | None = None


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
            records.append(
                IngestRecord(
                    source_pdf=pdf_path,
                    archived_to=dest,
                    content_hash=pdf_hash,
                )
            )
        except (OSError, shutil.Error) as exc:
            logger.exception("Failed to copy PDF %s to corpus", pdf_path)
            records.append(IngestRecord(source_pdf=pdf_path, error=str(exc)))
        except Exception as exc:
            logger.exception("Unexpected error during PDF ingestion for %s", pdf_path)
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
    except httpx.RequestError as exc:
        logger.exception("RAG client request failed for /index_directory: %s", exc)
        return None, str(exc)
    except httpx.HTTPStatusError as exc:
        logger.exception(
            "RAG client returned error status for /index_directory: %s", exc
        )
        return None, str(exc)
    except Exception as exc:
        logger.exception(
            "Unexpected error during RAG indexing for directory %s", directory
        )
        return None, str(exc)
    return response.json(), None


def reindex_corpus_directory(
    *,
    directory: Path,
    rag_url: str = DEFAULT_RAG_URL,
    timeout: float = 300.0,
    force: bool = True,
    extensions: list[str] | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Ask RAG to reindex a directory (removes stale chunks, optional force)."""
    body: dict[str, Any] = {
        "path": str(directory.expanduser().resolve()),
        "force": force,
    }
    if extensions is not None:
        body["extensions"] = extensions
    try:
        with make_sync_client(rag_url, timeout=timeout) as client:
            response = client.post("/reindex_directory", json=body)
        _ = response.raise_for_status()
    except httpx.RequestError as exc:
        logger.exception("RAG client request failed for /reindex_directory: %s", exc)
        return None, str(exc)
    except httpx.HTTPStatusError as exc:
        logger.exception(
            "RAG client returned error status for /reindex_directory: %s", exc
        )
        return None, str(exc)
    except Exception as exc:
        logger.exception(
            "Unexpected error during RAG reindexing for directory %s", directory
        )
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
    except httpx.RequestError as exc:
        logger.exception("RAG client request failed for /watch/status: %s", exc)
        return None, str(exc)
    except httpx.HTTPStatusError as exc:
        logger.exception("RAG client returned error status for /watch/status: %s", exc)
        return None, str(exc)
    except Exception as exc:
        logger.exception("Unexpected error while checking RAG watch status")
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


def update_article_registry(
    registry_path: Path,
    filename: str,
    entry: dict[str, Any],
) -> None:
    """Read registry YAML, upsert articles[filename]=entry, write back atomically."""
    data: dict[str, Any] = {"articles": {}}
    if registry_path.exists():
        raw: object = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and isinstance(raw.get("articles"), dict):
            data["articles"] = dict(raw["articles"])
    # Preserve YAML-native types instead of coercing everything to string.
    data["articles"][filename] = dict(entry)
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=registry_path.parent,
        prefix=".article_registry.",
        suffix=".yaml",
        delete=False,
    ) as fh:
        yaml.safe_dump(data, fh, default_flow_style=False, allow_unicode=True)
    tmp_path_obj = Path(fh.name)
    try:
        tmp_path_obj.replace(registry_path)
    except Exception as exc:
        logger.error("Article registry write failed: %s", exc)
        tmp_path_obj.unlink(missing_ok=True)
        raise


def extract_pdf_metadata(pdf_path: Path) -> dict[str, str]:
    """Extract title and author from PDF metadata via PyMuPDF; return empty dict on error."""
    try:
        import fitz

        doc = fitz.open(pdf_path)
        try:
            meta = doc.metadata or {}
            return {
                "title": meta.get("title") or "",
                "authors": meta.get("author") or "",
            }
        finally:
            doc.close()
    except OSError as exc:
        logger.warning("PDF metadata extraction failed for %s: %s", pdf_path, exc)
        return {}
    except Exception as exc:
        logger.error(
            "Unexpected error during PDF metadata extraction for %s: %s",
            pdf_path,
            exc,
        )
        return {}


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
