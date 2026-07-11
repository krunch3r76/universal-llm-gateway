"""Core OCR logic — PDF→image rendering, multi-provider vision, multi-page batching.

Shared between mcp-server (``extract_document``) and cortex-api
(``POST /documents/ocr/file`` and ``POST /documents/ocr/directory``).

Key design: ``stargate_url`` is an explicit parameter on every function that
needs to call a vision model. Callers resolve it once (typically from
``transport_utils.DEFAULT_STARGATE_URL``) and thread it through. No module-level
URL globals — the lib is environment-agnostic.

Supported providers (frontier-only — direct API, no OpenRouter):
- openai/gpt-5.4  (default — strongest overall, best for photographed documents with printed material)
- anthropic/claude-sonnet-4-*  (strong alternative, especially for complex layouts)
- xai/grok-4.20-*  (second to gpt-5.4 for photographs)
- xai/grok-4-*  (poor vision quality — avoid for scans/photographs)
- xai/grok-3-mini-*  (no vision support)

Images are resized adaptively per-provider via :mod:`_vision_resize`: a
saturation+edge classifier picks a JPEG quality floor (text vs. photo), and
the long side is stepped down until the per-provider token estimator fits
within ``token_budget`` (defaults to the profile's sweet-spot budget).
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from stargate_chat import call_stargate, extract_stargate_text
from universal_logging import get_logger

from ._vision_resize import profile_for_model, resize_to_budget

logger = get_logger(__name__)

_DEFAULT_DPI = 200
_MAX_PAGES_PER_BATCH = 4
_OCR_MODEL = "openai/gpt-5.4"
_OCR_MAX_TOKENS = 8192

_DEFAULT_OCR_PROMPT = (
    "Extract all text from this document. Preserve tables, columns, "
    "and structure. Return the text as-is, no interpretation."
)
_OCR_SYSTEM = (
    "You are a document OCR specialist. Extract text faithfully from the image."
)

_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".tiff", ".tif", ".webp", ".bmp"})
_SCANNABLE_SUFFIXES = frozenset({".pdf"}) | _IMAGE_SUFFIXES


def _pdf_page_to_base64(
    pdf_path: Path,
    page_num: int,
    *,
    dpi: int,
    model: str,
    token_budget: int | None,
) -> tuple[str, str, int]:
    """Render a PDF page, adaptively resize, return ``(base64, media_type, est_tokens)``."""
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
    jpeg_bytes, media_type, est_tokens = resize_to_budget(
        raw_png, model=model, token_budget=token_budget
    )
    return base64.b64encode(jpeg_bytes).decode("ascii"), media_type, est_tokens


def _image_file_to_base64(
    path: Path,
    *,
    model: str,
    token_budget: int | None,
) -> tuple[str, str, int]:
    """Read an image, adaptively resize, return ``(base64_data, media_type, est_tokens)``."""
    raw_bytes = path.read_bytes()
    jpeg_bytes, media_type, est_tokens = resize_to_budget(
        raw_bytes, model=model, token_budget=token_budget
    )
    return base64.b64encode(jpeg_bytes).decode("ascii"), media_type, est_tokens


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
    stargate_url: str,
    image_blocks: list[dict[str, Any]],
    user_prompt: str,
    *,
    system: str = _OCR_SYSTEM,
    model: str = _OCR_MODEL,
    max_tokens: int = _OCR_MAX_TOKENS,
) -> dict[str, Any]:
    """Send image blocks + text prompt to a vision model via Stargate.

    Routes through ``/v1/chat/completions`` so any Stargate-routable vision
    model works (Anthropic, OpenAI, xAI). Returns the raw OpenAI-format
    response dict, or ``{"error": "..."}`` on failure.
    """
    content: list[dict[str, Any]] = list(image_blocks)
    content.append({"type": "text", "text": user_prompt})
    messages = [{"role": "user", "content": content}]
    return call_stargate(
        stargate_url, messages, model=model, system=system, max_tokens=max_tokens
    )


def _usage_from_response(resp: dict[str, Any]) -> int:
    usage = resp.get("usage") or {}
    return usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0)


def _prompt_tokens_from_response(resp: dict[str, Any]) -> int | None:
    """Return actual ``prompt_tokens`` from the response, or None if absent."""
    usage = resp.get("usage") or {}
    pt = usage.get("prompt_tokens")
    return int(pt) if isinstance(pt, int | float) else None


def _log_estimate_vs_actual(
    *,
    model: str,
    estimated_image_tokens: int,
    actual_prompt_tokens: int | None,
    is_proxy: bool,
) -> None:
    """Log estimator drift for calibration (agent-bus thread 557).

    ``actual_prompt_tokens`` includes text overhead (system prompt + user
    text + image markers), so it will be higher than the image-only
    estimate by a small, roughly fixed amount. We log both raw and delta so
    operators can eyeball drift while we collect ≥50 samples.

    Level is WARN when ``is_proxy`` (xAI currently) so drift surfaces
    immediately; INFO otherwise.
    """
    if actual_prompt_tokens is None:
        return
    delta = actual_prompt_tokens - estimated_image_tokens
    msg = (
        "vision token estimate model=%s est_image_tokens=%d "
        "actual_prompt_tokens=%d delta=%+d"
    )
    if is_proxy:
        logger.warning(msg, model, estimated_image_tokens, actual_prompt_tokens, delta)
    else:
        logger.info(msg, model, estimated_image_tokens, actual_prompt_tokens, delta)


def ocr_pages(
    abs_path: Path,
    *,
    stargate_url: str,
    prompt: str = _DEFAULT_OCR_PROMPT,
    pages: list[int] | None = None,
    dpi: int = _DEFAULT_DPI,
    model: str = _OCR_MODEL,
    token_budget: int | None = None,
) -> dict[str, Any]:
    """OCR a PDF or image file, returning extracted text per page.

    For PDFs: renders each page, adaptively resizes per-provider profile,
    batches into groups of 4, sends to a vision model via Stargate. For
    images: sends the (resized) image directly.

    ``stargate_url`` is required and threaded through to every Stargate call.
    Resolve via ``transport_utils.DEFAULT_STARGATE_URL`` at the call site.

    ``token_budget`` (image-only token target) defaults to the provider
    profile's sweet-spot budget (e.g. ~1615 for OpenAI's 1536² sweet spot
    with ~10% headroom).
    """
    is_image = abs_path.suffix.lower() in _IMAGE_SUFFIXES
    profile = profile_for_model(model)

    if is_image:
        data, media, est = _image_file_to_base64(
            abs_path, model=model, token_budget=token_budget
        )
        blocks = _build_image_blocks([(data, media)])
        resp = _call_vision(stargate_url, blocks, prompt, model=model)
        _log_estimate_vs_actual(
            model=model,
            estimated_image_tokens=est,
            actual_prompt_tokens=_prompt_tokens_from_response(resp),
            is_proxy=profile.is_proxy_estimator,
        )
        text = extract_stargate_text(resp)
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
        rendered = [
            _pdf_page_to_base64(
                abs_path, idx, dpi=dpi, model=model, token_budget=token_budget
            )
            for idx in batch
        ]
        images = [(data, media) for data, media, _est in rendered]
        batch_est = sum(est for _data, _media, est in rendered)
        blocks = _build_image_blocks(images)

        page_labels = ", ".join(str(idx + 1) for idx in batch)
        batch_prompt = f"Pages: {page_labels}\n\n{prompt}"
        resp = _call_vision(stargate_url, blocks, batch_prompt, model=model)
        _log_estimate_vs_actual(
            model=model,
            estimated_image_tokens=batch_est,
            actual_prompt_tokens=_prompt_tokens_from_response(resp),
            is_proxy=profile.is_proxy_estimator,
        )
        batch_text = extract_stargate_text(resp)
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
