"""Core OCR logic: PDF→image rendering, Claude Vision calls, multi-page batching.

Uses PyMuPDF (fitz) for PDF→PNG conversion and Anthropic Claude Vision for
text extraction. Supports both generic OCR and structured financial extraction.

All images are auto-resized to fit within _MAX_IMAGE_DIMENSION before encoding
to avoid Anthropic API 400 errors on oversized payloads (high-res scans,
high-DPI PDF renders).
"""

from __future__ import annotations

import base64
import io
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
_MAX_IMAGE_DIMENSION = 1600

_DEFAULT_OCR_PROMPT = (
    "Extract all text from this document. Preserve tables, columns, "
    "and structure. Return the text as-is, no interpretation."
)
_OCR_SYSTEM = (
    "You are a document OCR specialist. Extract text faithfully from the image."
)

_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".tiff", ".tif", ".webp", ".bmp"})


def _resize_to_limit(
    png_bytes: bytes, *, max_dim: int = _MAX_IMAGE_DIMENSION
) -> tuple[bytes, str]:
    """Resize image bytes so the longest side ≤ *max_dim*; return (jpeg_bytes, media_type).

    If already within limits, returns the original bytes as JPEG for consistency.
    Converts RGBA/P modes to RGB for JPEG compatibility.
    """
    from PIL import Image as PILImage

    img = PILImage.open(io.BytesIO(png_bytes))
    w, h = img.size
    if max(w, h) > max_dim:
        img.thumbnail((max_dim, max_dim), PILImage.Resampling.LANCZOS)
        logger.info("Resized %dx%d → %dx%d for OCR", w, h, img.width, img.height)
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85, optimize=True)
    return buf.getvalue(), "image/jpeg"


def _pdf_page_to_base64(
    pdf_path: Path, page_num: int, dpi: int = _DEFAULT_DPI
) -> tuple[str, str]:
    """Render a PDF page to image, resize to fit API limits, return (base64, media_type)."""
    import fitz

    doc = fitz.open(str(pdf_path))
    try:
        page = doc[page_num]
        zoom = dpi / 72
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat)
        raw_png = pix.tobytes("png")
    finally:
        doc.close()
    resized_bytes, media_type = _resize_to_limit(raw_png)
    return base64.b64encode(resized_bytes).decode("ascii"), media_type


def _image_file_to_base64(path: Path) -> tuple[str, str]:
    """Read an image file, resize to fit API limits, return (base64_data, media_type)."""
    raw_bytes = path.read_bytes()
    resized_bytes, media_type = _resize_to_limit(raw_bytes)
    return base64.b64encode(resized_bytes).decode("ascii"), media_type


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
        images = [_pdf_page_to_base64(abs_path, idx, dpi) for idx in batch]
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
        images = [_pdf_page_to_base64(abs_path, i, dpi) for i in range(total)]

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
