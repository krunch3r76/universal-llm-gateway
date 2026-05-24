"""``extract_directory`` MCP tool — batch OCR relay.

Thin HTTP relay to cortex-api ``/documents/ocr/directory``; orchestration
lives in :mod:`ocr_core` / ``libs/cortex_store/routes/documents.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from transport_utils import DEFAULT_CORTEX_URL, make_sync_client
from universal_logging import get_logger

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = get_logger(__name__)

_OCR_TIMEOUT = 1800.0  # 30 minutes — directory batches can run long


def _relay(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    """POST ``payload`` to cortex-api ``path`` and return the JSON body."""
    with make_sync_client(DEFAULT_CORTEX_URL, timeout=_OCR_TIMEOUT) as client:
        resp = client.post(path, json=payload)
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail", resp.text[:500])
        except ValueError:
            # JSON decode failed (json.JSONDecodeError subclasses ValueError);
            # fall back to raw text. Logged at debug since the upstream
            # status code itself is the primary signal.
            logger.debug(
                "extract_directory: cortex-api %d body not JSON", resp.status_code
            )
            detail = resp.text[:500]
        return {"error": f"cortex-api {resp.status_code}", "detail": detail}
    return resp.json()


def register_extract_directory_tools(mcp: FastMCP) -> None:
    """Register the ``extract_directory`` batch OCR tool."""

    @mcp.tool(title="Extract Directory")
    def extract_directory(
        directory: str,
        prompt: str = "",
        dpi: int = 200,
        model: str = "",
        token_budget: int | None = None,
    ) -> dict[str, Any]:
        """Batch OCR all PDFs and images in a directory.

        Available via: dispatch(tool="extract_directory", ...)

        Cost warning: runs one vision model call per page per file. Prefer
        ``extract_document`` on individual files when you need persistent
        sidecar markdown; use this tool for bulk directory passes only.

        Args:
            directory: Directory path relative to ``/data/files/``.
            prompt: Extraction instruction (default: generic text extraction).
            dpi: Render resolution for PDFs (default: 200).
            model: Model override (default: openai/gpt-5.4).
            token_budget: Image-only token budget per page.
        """
        payload: dict[str, Any] = {"directory": directory, "dpi": dpi}
        if prompt:
            payload["prompt"] = prompt
        if model:
            payload["model"] = model
        if token_budget is not None:
            payload["token_budget"] = token_budget

        logger.info("extract_directory: %s", directory)
        return _relay("/documents/ocr/directory", payload)
