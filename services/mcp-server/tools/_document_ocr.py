"""Core OCR logic: PDF→image rendering, Claude Vision calls, multi-page batching.

Uses PyMuPDF (fitz) for PDF→PNG conversion and Anthropic Claude Vision for
text extraction. Supports both generic OCR and structured financial extraction.
"""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Any

from .llm import _call_anthropic

logger = logging.getLogger(__name__)

_DEFAULT_DPI = 200
_MAX_PAGES_PER_BATCH = 4
_OCR_MODEL = "claude-sonnet-4-20250514"
_OCR_MAX_TOKENS = 8192

_DEFAULT_OCR_PROMPT = (
    "Extract all text from this document. Preserve tables, columns, "
    "and structure. Return the text as-is, no interpretation."
)
_OCR_SYSTEM = (
    "You are a document OCR specialist. Extract text faithfully from the image."
)

_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".tiff", ".tif", ".webp", ".bmp"})


def _pdf_page_to_base64(pdf_path: Path, page_num: int, dpi: int = _DEFAULT_DPI) -> str:
    """Render a single PDF page to PNG and return base64-encoded bytes."""
    import fitz

    doc = fitz.open(str(pdf_path))
    try:
        page = doc[page_num]
        zoom = dpi / 72
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat)
        return base64.b64encode(pix.tobytes("png")).decode("ascii")
    finally:
        doc.close()


def _image_file_to_base64(path: Path) -> tuple[str, str]:
    """Read an image file and return (base64_data, media_type)."""
    suffix = path.suffix.lower()
    media_map = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".tiff": "image/tiff",
        ".tif": "image/tiff",
        ".webp": "image/webp",
        ".bmp": "image/bmp",
    }
    media_type = media_map.get(suffix, "image/png")
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return data, media_type


def _pdf_page_count(pdf_path: Path) -> int:
    import fitz

    doc = fitz.open(str(pdf_path))
    try:
        return len(doc)
    finally:
        doc.close()


def _build_image_blocks(
    images: list[tuple[str, str]],
) -> list[dict[str, Any]]:
    """Build Anthropic image content blocks from (base64_data, media_type) pairs."""
    blocks: list[dict[str, Any]] = []
    for data, media in images:
        blocks.append(
            {
                "type": "image",
                "source": {"type": "base64", "media_type": media, "data": data},
            }
        )
    return blocks


def _call_vision(
    image_blocks: list[dict[str, Any]],
    user_prompt: str,
    system: str = _OCR_SYSTEM,
    model: str = _OCR_MODEL,
    max_tokens: int = _OCR_MAX_TOKENS,
) -> dict[str, Any]:
    """Send image blocks + text prompt to Claude Vision."""
    content: list[dict[str, Any]] = list(image_blocks)
    content.append({"type": "text", "text": user_prompt})
    payload: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": content}],
        "system": system,
    }
    return _call_anthropic(payload, requested_model=model)


def _extract_text_from_response(resp: dict[str, Any]) -> str:
    """Pull concatenated text from Anthropic response content blocks."""
    if "error" in resp:
        return f"[OCR error: {resp['error']}]"
    return "".join(
        b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text"
    )


def _usage_from_response(resp: dict[str, Any]) -> int:
    usage = resp.get("usage", {})
    return usage.get("input_tokens", 0) + usage.get("output_tokens", 0)


def ocr_pages(
    abs_path: Path,
    *,
    prompt: str = _DEFAULT_OCR_PROMPT,
    pages: list[int] | None = None,
    dpi: int = _DEFAULT_DPI,
    model: str = _OCR_MODEL,
) -> dict[str, Any]:
    """OCR a PDF or image file, returning extracted text per page.

    For PDFs: renders each page to PNG, batches into groups of 4, sends to
    Claude Vision. For images: sends the image directly.
    """
    is_image = abs_path.suffix.lower() in _IMAGE_SUFFIXES

    if is_image:
        data, media = _image_file_to_base64(abs_path)
        blocks = _build_image_blocks([(data, media)])
        resp = _call_vision(blocks, prompt, model=model)
        text = _extract_text_from_response(resp)
        tokens = _usage_from_response(resp)
        return {
            "pages": 1,
            "text": text,
            "per_page": [{"page": 1, "text": text, "tokens_used": tokens}],
            "model": resp.get("model", model),
            "total_tokens": tokens,
        }

    total_pages = _pdf_page_count(abs_path)
    page_indices = [p - 1 for p in pages] if pages else list(range(total_pages))

    per_page: list[dict[str, Any]] = []
    all_text_parts: list[str] = []
    total_tokens = 0
    used_model = model

    for batch_start in range(0, len(page_indices), _MAX_PAGES_PER_BATCH):
        batch = page_indices[batch_start : batch_start + _MAX_PAGES_PER_BATCH]
        images = [
            (_pdf_page_to_base64(abs_path, idx, dpi), "image/png") for idx in batch
        ]
        blocks = _build_image_blocks(images)

        page_labels = ", ".join(str(idx + 1) for idx in batch)
        batch_prompt = f"Pages: {page_labels}\n\n{prompt}"
        resp = _call_vision(blocks, batch_prompt, model=model)
        batch_text = _extract_text_from_response(resp)
        batch_tokens = _usage_from_response(resp)
        used_model = resp.get("model", model)

        if len(batch) == 1:
            page_num = batch[0] + 1
            per_page.append(
                {"page": page_num, "text": batch_text, "tokens_used": batch_tokens}
            )
            all_text_parts.append(f"\n--- Page {page_num} ---\n{batch_text}")
        else:
            per_page_tokens = batch_tokens // len(batch)
            for idx in batch:
                page_num = idx + 1
                per_page.append(
                    {"page": page_num, "text": "", "tokens_used": per_page_tokens}
                )
            all_text_parts.append(batch_text)

        total_tokens += batch_tokens

    return {
        "pages": len(page_indices),
        "text": "\n".join(all_text_parts).strip(),
        "per_page": per_page,
        "model": used_model,
        "total_tokens": total_tokens,
    }


def ocr_structured(
    abs_path: Path,
    statement_type: str,
    *,
    dpi: int = _DEFAULT_DPI,
    model: str = _OCR_MODEL,
) -> dict[str, Any]:
    """OCR + structured extraction: render PDF pages, send with schema prompt.

    Returns the same JSON shape as finance_parse_statement (Phase 2) so it
    feeds directly into finance_ingest_statement (Phase 3).
    """
    from ._finance_schemas import STATEMENT_SCHEMAS

    schema = STATEMENT_SCHEMAS.get(statement_type)
    if schema is None:
        return {"error": f"Unknown statement_type: {statement_type!r}"}

    schema_json = json.dumps(schema, indent=2)
    system = (
        "You are a financial document parser. Extract structured data from "
        "this scanned document. Return ONLY valid JSON with no preamble, "
        "no markdown, no explanation."
    )
    user_prompt = (
        f"Statement type: {statement_type}\n\n"
        f"Extract data from this scanned document image(s).\n\n"
        f"Return JSON matching this schema:\n{schema_json}"
    )

    is_image = abs_path.suffix.lower() in _IMAGE_SUFFIXES
    if is_image:
        data, media = _image_file_to_base64(abs_path)
        images = [(data, media)]
    else:
        total = _pdf_page_count(abs_path)
        images = [
            (_pdf_page_to_base64(abs_path, i, dpi), "image/png") for i in range(total)
        ]

    all_parsed: dict[str, Any] = {}
    for batch_start in range(0, len(images), _MAX_PAGES_PER_BATCH):
        batch = images[batch_start : batch_start + _MAX_PAGES_PER_BATCH]
        blocks = _build_image_blocks(batch)
        resp = _call_vision(blocks, user_prompt, system=system, model=model)
        raw = _extract_text_from_response(resp)

        import re

        text = raw.strip()
        m = re.match(r"^```(?:json)?\s*\n?(.*?)\n?\s*```$", text, re.DOTALL)
        if m:
            text = m.group(1).strip()

        try:
            batch_parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            return {
                "error": f"JSON parse failed: {exc}",
                "raw_response": raw,
                "statement_type": statement_type,
            }
        if not all_parsed:
            all_parsed = batch_parsed
        else:
            for key, val in batch_parsed.items():
                if isinstance(val, list) and isinstance(all_parsed.get(key), list):
                    all_parsed[key].extend(val)

    return {
        "statement_type": statement_type,
        "parsed": all_parsed,
        "model": model,
    }


def has_text_layer(path: Path) -> bool:
    """Check if a PDF has extractable text content."""
    import fitz

    doc = fitz.open(str(path))
    try:
        for page in doc:
            if page.get_text().strip():
                return True
        return False
    finally:
        doc.close()
