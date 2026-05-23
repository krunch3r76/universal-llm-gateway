"""Document OCR routes — vision-model OCR via shared ``ocr_core``.

Two endpoints, both used by thin MCP tool handlers (``document_ocr`` and
``document_ocr_directory``) that pure-relay HTTP to here:

- ``POST /documents/ocr/file``      — single PDF or image
- ``POST /documents/ocr/directory`` — batch every scannable file under a dir

File access uses ``CORTEX_FILES_ROOT`` (same volume the MCP server's
``/data/files`` mount targets — see the ``_FILES_ROOT`` warning in
``dispatch_ops/_shared.py``). The vision call goes to Stargate via
``transport_utils.DEFAULT_STARGATE_URL``.

Event signals (``mcp.document.ocr.*``) are emitted from here, not from the
MCP handler, so observability lands at the process where work actually runs.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, status
from ocr_core import SCANNABLE_SUFFIXES, ocr_directory, ocr_pages
from pydantic import BaseModel, Field
from transport_utils import DEFAULT_STARGATE_URL
from universal_logging import get_logger

from ..dispatch_ops._shared import _FILES_ROOT, record

logger = get_logger("cortex-api.documents")

router = APIRouter(prefix="/documents", tags=["documents"])


# ---------------------------------------------------------------------------
# Request/response models
# ---------------------------------------------------------------------------


class OcrFileRequest(BaseModel):
    """Request for ``POST /documents/ocr/file``."""

    path: str = Field(
        ...,
        description="File path relative to CORTEX_FILES_ROOT.",
    )
    prompt: str = Field(
        "",
        description="Extraction prompt. Empty string uses ocr_core's default.",
    )
    pages: list[int] | None = Field(
        None,
        description="1-based page numbers to OCR. None = all pages.",
    )
    dpi: int = Field(
        200,
        ge=72,
        le=600,
        description="PDF render resolution.",
    )
    model: str = Field(
        "",
        description="Model override. Empty uses ocr_core default (openai/gpt-5.4).",
    )
    token_budget: int | None = Field(
        None,
        description="Per-image token budget. None uses provider profile default.",
    )


class OcrDirectoryRequest(BaseModel):
    """Request for ``POST /documents/ocr/directory``."""

    directory: str = Field(
        ...,
        description="Directory path relative to CORTEX_FILES_ROOT.",
    )
    prompt: str = Field("", description="Extraction prompt.")
    dpi: int = Field(200, ge=72, le=600)
    model: str = Field("", description="Model override.")
    token_budget: int | None = Field(None)


class OcrDirectorySummary(BaseModel):
    total_files: int
    successful: int
    failed: int


class OcrFileError(BaseModel):
    path: str
    error: str


class OcrDirectoryResponse(BaseModel):
    """Response payload — matches the historical MCP-tool return shape."""

    directory: str
    results: list[dict[str, Any]]
    errors: list[OcrFileError]
    summary: OcrDirectorySummary


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_under_root(rel: str, *, kind: str) -> Path:
    """Resolve ``rel`` strictly inside ``CORTEX_FILES_ROOT``. Raise 400 on escape."""
    files_root_resolved = _FILES_ROOT.resolve()
    abs_path = (_FILES_ROOT / rel).resolve()
    try:
        abs_path.relative_to(files_root_resolved)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{kind} {rel!r} resolves outside CORTEX_FILES_ROOT",
        ) from exc
    return abs_path


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/ocr/file",
    response_model=dict,
    status_code=status.HTTP_200_OK,
)
def ocr_file_endpoint(body: OcrFileRequest) -> dict[str, Any]:
    """OCR a single PDF or image via a frontier vision model (Stargate-routed).

    Renders pages, calls the vision model, returns text + per-page breakdown.
    Errors:
        404 if file missing
        400 if path escapes CORTEX_FILES_ROOT or unsupported suffix
        502 if vision model upstream errors (returned in payload, not raised)
    """
    t0 = time.monotonic()
    record("mcp.document.ocr.called", path=body.path)

    abs_path = _resolve_under_root(body.path, kind="path")
    if not abs_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File not found: {body.path!r}",
        )
    if abs_path.suffix.lower() not in SCANNABLE_SUFFIXES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file type: {abs_path.suffix!r}",
        )

    kwargs: dict[str, Any] = {
        "stargate_url": DEFAULT_STARGATE_URL,
        "dpi": body.dpi,
    }
    if body.prompt:
        kwargs["prompt"] = body.prompt
    if body.pages:
        kwargs["pages"] = body.pages
    if body.model:
        kwargs["model"] = body.model
    if body.token_budget is not None:
        kwargs["token_budget"] = body.token_budget

    result = ocr_pages(abs_path, **kwargs)
    result["path"] = body.path

    elapsed = time.monotonic() - t0
    record(
        "mcp.document.ocr.completed",
        path=body.path,
        pages=result["pages"],
        total_tokens=result["total_tokens"],
        duration_s=round(elapsed, 3),
    )
    return result


@router.post(
    "/ocr/directory",
    response_model=OcrDirectoryResponse,
    status_code=status.HTTP_200_OK,
)
def ocr_directory_endpoint(body: OcrDirectoryRequest) -> OcrDirectoryResponse:
    """Batch OCR every scannable file in a directory (one level deep).

    Per-file failures are isolated — they appear in the ``errors`` list but
    do not abort the batch. The endpoint only raises HTTP 4xx for setup
    failures (directory missing, not a directory, no scannable files).
    """
    t0 = time.monotonic()
    record("mcp.document.ocr.directory.called", directory=body.directory)

    abs_dir = _resolve_under_root(body.directory, kind="directory")

    try:
        result = ocr_directory(
            abs_dir,
            stargate_url=DEFAULT_STARGATE_URL,
            files_root=_FILES_ROOT.resolve(),
            prompt=body.prompt,
            dpi=body.dpi,
            model=body.model,
            token_budget=body.token_budget,
        )
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    for err in result["errors"]:
        record(
            "mcp.document.ocr.file.failed",
            path=err["path"],
            error=err["error"],
        )

    elapsed = time.monotonic() - t0
    record(
        "mcp.document.ocr.directory.completed",
        directory=body.directory,
        file_count=result["summary"]["total_files"],
        success_count=result["summary"]["successful"],
        error_count=result["summary"]["failed"],
        duration_s=round(elapsed, 3),
    )

    return OcrDirectoryResponse(
        directory=body.directory,
        results=result["results"],
        errors=[OcrFileError(**e) for e in result["errors"]],
        summary=OcrDirectorySummary(**result["summary"]),
    )
