"""Core OCR logic: PDF→image rendering, multi-provider vision, multi-page batching.

Uses PyMuPDF (fitz) for PDF→PNG conversion and frontier vision models (via
Stargate) for text extraction.  Supports both generic OCR and structured
financial extraction.

Supported providers (frontier-only — direct API, no OpenRouter):
- openai/gpt-5.4  (default — strongest overall, best for photographed documents with printed material)
- anthropic/claude-sonnet-4-*  (strong alternative, especially for complex layouts)
- xai/grok-4.20-*  (second to gpt-5.4 for photographs)
- xai/grok-4-*  (poor vision quality — avoid for scans/photographs)
- xai/grok-3-mini-*  (no vision support)

All images are auto-resized to fit within _MAX_IMAGE_DIMENSION before encoding
to avoid provider API errors on oversized payloads.
"""

from __future__ import annotations

import base64
import io
import logging
from pathlib import Path
from typing import Any

from .llm import _call_stargate

logger = logging.getLogger(__name__)

_DEFAULT_DPI = 200
_MAX_PAGES_PER_BATCH = 4
_OCR_MODEL = "openai/gpt-5.4"
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
    """Build OpenAI-format image content blocks from (base64_data, media_type) pairs."""
    return [
        {"type": "image_url", "image_url": {"url": f"data:{media};base64,{data}"}}
        for data, media in images
    ]


def _call_vision(
    image_blocks: list[dict[str, Any]],
    user_prompt: str,
    system: str = _OCR_SYSTEM,
    model: str = _OCR_MODEL,
    max_tokens: int = _OCR_MAX_TOKENS,
) -> dict[str, Any]:
    """Send image blocks + text prompt to a vision model via Stargate.

    Routes through ``/v1/chat/completions`` so any Stargate-routable vision
    model works (Anthropic, OpenAI, xAI).  Returns the raw OpenAI-format
    response dict, or ``{"error": "..."}`` on failure.
    """
    content: list[dict[str, Any]] = list(image_blocks)
    content.append({"type": "text", "text": user_prompt})
    messages = [{"role": "user", "content": content}]
    return _call_stargate(messages, model=model, system=system, max_tokens=max_tokens)


def _extract_text_from_response(resp: dict[str, Any]) -> str:
    """Pull assistant text from an OpenAI-format chat completion response."""
    if "error" in resp:
        return f"[OCR error: {resp['error']}]"
    choices = resp.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    return message.get("content") or ""


def _usage_from_response(resp: dict[str, Any]) -> int:
    usage = resp.get("usage") or {}
    return usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0)


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
    a vision model via Stargate. For images: sends the image directly.
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
