"""Directory-level OCR orchestration.

Enumerates scannable files under a directory, OCRs each via :func:`ocr_pages`,
accumulates results and per-file errors. The cortex-api ``POST
/documents/ocr/directory`` endpoint thin-wraps this orchestrator; the thin MCP
``extract_directory`` MCP tool relays to the cortex-api endpoint.

Structurally separated from ``_core`` so the per-file OCR primitive (used
directly by ``extract_document`` in mcp-server) doesn't drag in the
batch concerns (per-file error accumulation, summary aggregation, relative-path
computation).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ._core import _SCANNABLE_SUFFIXES, ocr_pages

logger = logging.getLogger(__name__)


def ocr_directory(
    abs_dir: Path,
    *,
    stargate_url: str,
    files_root: Path,
    prompt: str = "",
    dpi: int = 200,
    model: str = "",
    token_budget: int | None = None,
) -> dict[str, Any]:
    """Batch OCR every scannable file under ``abs_dir``.

    Recurses one level (``iterdir``) — matches the existing single-directory
    semantics of the MCP ``extract_directory`` tool. Returns paths
    relative to ``files_root`` so cross-service references stay portable.

    ``stargate_url`` is threaded through to ``ocr_pages`` for every file. The
    OCR model defaults to ``ocr_core._core._OCR_MODEL`` (passed as empty
    string by the caller → resolved inside ``ocr_pages``).

    Raises:
        FileNotFoundError: if ``abs_dir`` does not exist or contains no
            scannable files.
        ValueError: if ``abs_dir`` is not a directory.

    Returns:
        ``{"results": list, "errors": list, "summary": {total_files, successful, failed}}``.
        Each ``results`` entry is the ``ocr_pages`` return dict with a ``path``
        field (relative to ``files_root``) injected. Each ``errors`` entry is
        ``{"path": str, "error": str}``.
    """
    if not abs_dir.exists():
        raise FileNotFoundError(f"Directory not found: {abs_dir}")
    if not abs_dir.is_dir():
        raise ValueError(f"Not a directory: {abs_dir}")

    files = sorted(
        f for f in abs_dir.iterdir() if f.suffix.lower() in _SCANNABLE_SUFFIXES
    )
    if not files:
        raise FileNotFoundError(f"No scannable files found in {abs_dir}")

    results: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    ocr_kwargs: dict[str, Any] = {"stargate_url": stargate_url, "dpi": dpi}
    if prompt:
        ocr_kwargs["prompt"] = prompt
    if model:
        ocr_kwargs["model"] = model
    if token_budget is not None:
        ocr_kwargs["token_budget"] = token_budget

    for file_path in files:
        try:
            rel_path = str(file_path.relative_to(files_root))
        except ValueError:
            # File lives outside files_root — fall back to absolute path string
            # so the caller still sees something meaningful.
            rel_path = str(file_path)

        try:
            result = ocr_pages(file_path, **ocr_kwargs)
            result["path"] = rel_path
            results.append(result)
        except Exception as exc:  # noqa: BLE001 — per-file isolation is intentional
            logger.warning("Failed to OCR %s: %s", rel_path, exc)
            errors.append({"path": rel_path, "error": str(exc)})

    return {
        "results": results,
        "errors": errors,
        "summary": {
            "total_files": len(files),
            "successful": len(results),
            "failed": len(errors),
        },
    }
