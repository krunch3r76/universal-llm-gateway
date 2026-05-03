"""Document OCR tool registration — thin HTTP relays to cortex-api.

Both ``document_ocr`` (single file) and ``document_ocr_directory`` (batch) are
pure HTTP relays to ``libs/cortex_store/routes/documents.py``. The orchestration,
file enumeration, vision-model calls, and event emission all live behind the
cortex-api endpoints, satisfying the [mcp] invariant for the entire OCR
surface: handler bodies contain no business logic.

Why cortex-api: it has both ``CORTEX_FILES_ROOT`` access (same volume the
mcp-server container's ``/data/files`` mount targets) and an established
Stargate-call pattern. The shared :mod:`ocr_core` lib is environment-agnostic
so the same code runs from cortex-api here and (via thin wrappers) from any
future caller.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from transport_utils import DEFAULT_CORTEX_URL, make_sync_client

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger(__name__)

# Long timeout: directory batches can run minutes — one vision call per page
# per file. Cortex-api UDS round-trip itself is fast; the inner work isn't.
_OCR_TIMEOUT = 1800.0  # 30 minutes


def _relay(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    """POST ``payload`` to cortex-api ``path`` and return the JSON body.

    Returns ``{"error": ..., "detail": ...}`` on 4xx/5xx so callers always
    receive a JSON-shaped response without raising into the FastMCP layer.
    """
    with make_sync_client(DEFAULT_CORTEX_URL, timeout=_OCR_TIMEOUT) as client:
        resp = client.post(path, json=payload)
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail", resp.text[:500])
        except Exception:
            detail = resp.text[:500]
        return {"error": f"cortex-api {resp.status_code}", "detail": detail}
    return resp.json()


def register_document_ocr_tools(mcp: FastMCP) -> None:
    """Register general-purpose document OCR tools (public dispatch)."""

    @mcp.tool(title="Document OCR")
    def document_ocr(
        path: str,
        prompt: str = "",
        pages: list[int] | None = None,
        dpi: int = 200,
        model: str = "",
        token_budget: int | None = None,
    ) -> dict[str, Any]:
        """OCR a scanned PDF or image via a frontier vision model (Stargate-routed).

        Available via: dispatch(tool="document_ocr", arguments='{"path": "..."}')

        Use when: fs(op="read") returns empty or garbled text for a PDF (no text
        layer), or you need to extract text from photographs or scanned documents.
        Handles rendering, resizing, batching, and vision model routing server-side
        — do not extract base64 manually.

        Images are resized adaptively per provider: a saturation+edge classifier
        picks JPEG quality (text vs. photo), and the long side is stepped down
        until the per-provider image-token estimate fits ``token_budget``.
        Defaults harmonize with the selected model's profile (OpenAI sweet
        spot ≈1536px long side, ~1615-token image budget).

        For financial documents that need structured JSON, prefer
        `document_ocr_structured` (now available as primary tool or via dispatch).

        Args:
            path: PDF or image path relative to /data/files/.
            prompt: Extraction instruction (default: generic text extraction).
            pages: 1-based page numbers to process (default: all).
            dpi: Render resolution for PDFs (default: 200).
            model: Model override (default: openai/gpt-5.4). Any Stargate-routable
                vision model works. Prefer openai/gpt-5.5 for degraded scans,
                complex tables, or photographed documents — its native omnimodal
                architecture gives a modest but real quality edge at 2× cost.
                Other options: anthropic/claude-sonnet-4, xai/grok-4.20-0309-reasoning.
            token_budget: Image-only token budget per page (default: derived
                from provider profile). Raise for max-fidelity single pages;
                lower for tight batch/bulk runs. Excludes text-prompt overhead.
        """
        payload: dict[str, Any] = {"path": path, "dpi": dpi}
        if prompt:
            payload["prompt"] = prompt
        if pages:
            payload["pages"] = pages
        if model:
            payload["model"] = model
        if token_budget is not None:
            payload["token_budget"] = token_budget

        result = _relay("/documents/ocr/file", payload)
        if "error" not in result:
            result["_next"] = (
                "If extracted text contains facts about known entities, "
                "seed via cortex assert or ingest_document. "
                "If the source document lacks a document: entity in Cortex, "
                "create one via cortex entity_create"
            )
        return result

    @mcp.tool(title="Document OCR (Directory)")
    def document_ocr_directory(
        directory: str,
        prompt: str = "",
        dpi: int = 200,
        model: str = "",
        token_budget: int | None = None,
    ) -> dict[str, Any]:
        """Batch OCR all PDFs and images in a directory.

        Available via: dispatch(tool="document_ocr_directory", ...)

        Cost warning: runs one vision model call per page per file — a
        directory with 20 multi-page PDFs can consume thousands of tokens.
        Prefer document_ocr on individual files when possible. Lowering
        ``token_budget`` tightens per-image cost for bulk runs.

        Images are resized adaptively per provider (see ``document_ocr``
        for full behavior).

        Args:
            directory: Directory path relative to /data/files/.
            prompt: Extraction instruction (default: generic text extraction).
            dpi: Render resolution for PDFs (default: 200).
            model: Model override (default: openai/gpt-5.4). Any Stargate-routable
                vision model works — e.g. anthropic/claude-sonnet-4,
                xai/grok-4.20-0309-reasoning.
            token_budget: Image-only token budget per page (default: derived
                from provider profile).
        """
        payload: dict[str, Any] = {"directory": directory, "dpi": dpi}
        if prompt:
            payload["prompt"] = prompt
        if model:
            payload["model"] = model
        if token_budget is not None:
            payload["token_budget"] = token_budget

        return _relay("/documents/ocr/directory", payload)
