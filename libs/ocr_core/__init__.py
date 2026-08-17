"""ocr_core — shared OCR primitives for mcp-server and cortex-api.

Public API:
    ocr_pages(abs_path, *, stargate_url, prompt=..., pages=None, dpi=200,
              model="openai/gpt-5.4", token_budget=None) -> dict
        Single-file OCR: PDF or image. Renders pages, calls vision model
        through Stargate, returns text + per-page breakdown.

    ocr_directory(abs_dir, *, stargate_url, files_root, prompt="",
                  dpi=200, model="", token_budget=None) -> dict
        Batch OCR every scannable file under ``abs_dir`` (one level deep).
        Returns ``{results, errors, summary}``.

    profile_for_model(model) -> VisionProfile
        Look up provider-specific resize/budget profile.

    resize_to_budget(raw_bytes, *, model, token_budget=None) -> (bytes, str, int)
        Adaptive image resize to fit the per-provider token budget.

Constants:
    IMAGE_SUFFIXES, SCANNABLE_SUFFIXES — accepted file extensions
    OCR_MODEL — default vision model
    OCR_SYSTEM, DEFAULT_OCR_PROMPT — default prompts

Internal helpers (re-exported for ``extract_document_structured``):
``_call_vision``, ``_build_image_blocks``, ``_image_file_to_base64``,
``_pdf_page_to_base64``, ``_pdf_page_count``, ``_MAX_PAGES_PER_BATCH``,
``_OCR_MODEL``, ``_OCR_SYSTEM``, ``_DEFAULT_OCR_PROMPT``, ``_IMAGE_SUFFIXES``.

Stargate chat transport lives in :mod:`stargate_chat` — ``ocr_core`` calls it
internally via ``_call_vision``. Shared with cortex-api via
``POST /documents/ocr/*`` (``libs/cortex_store/routes/documents.py``).
"""

from ._core import (
    _DEFAULT_OCR_PROMPT,
    _IMAGE_SUFFIXES,
    _MAX_PAGES_PER_BATCH,
    _OCR_MODEL,
    _OCR_SYSTEM,
    _SCANNABLE_SUFFIXES,
    _build_image_blocks,
    _call_vision,
    _image_file_to_base64,
    _pdf_page_count,
    _pdf_page_to_base64,
    has_text_layer,
    ocr_pages,
)
from ._directory import ocr_directory
from ._vision_resize import (
    VisionProfile,
    classify_content,
    profile_for_model,
    resize_to_budget,
)

# Harvest nominates these manage slugs when this lib lands (package-grain).
CONSUMERS: tuple[str, ...] = ('mcp',)

# Public, non-underscored aliases for new callers.
IMAGE_SUFFIXES = _IMAGE_SUFFIXES
SCANNABLE_SUFFIXES = _SCANNABLE_SUFFIXES
OCR_MODEL = _OCR_MODEL
OCR_SYSTEM = _OCR_SYSTEM
DEFAULT_OCR_PROMPT = _DEFAULT_OCR_PROMPT

__all__ = [
    # Public API
    "ocr_pages",
    "ocr_directory",
    "has_text_layer",
    "profile_for_model",
    "resize_to_budget",
    "classify_content",
    "VisionProfile",
    # Constants (public alias)
    "IMAGE_SUFFIXES",
    "SCANNABLE_SUFFIXES",
    "OCR_MODEL",
    "OCR_SYSTEM",
    "DEFAULT_OCR_PROMPT",
    # Internal re-exports for extract_document_structured
    "_IMAGE_SUFFIXES",
    "_SCANNABLE_SUFFIXES",
    "_MAX_PAGES_PER_BATCH",
    "_OCR_MODEL",
    "_OCR_SYSTEM",
    "_DEFAULT_OCR_PROMPT",
    "_call_vision",
    "_build_image_blocks",
    "_image_file_to_base64",
    "_pdf_page_to_base64",
    "_pdf_page_count",
]
