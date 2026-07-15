"""Per-page OCR gate for ``extract_document`` when ``pages=`` is set.

Friction 24428: whole-PDF ``has_text_layer`` classifies mixed PDFs as
``text_pdf``, so image-locked selected pages never reach vision OCR.
When the caller names specific pages, thin/empty pages that carry images
route through ``ocr_pages``; text-rich selected pages stay on pymupdf.
Full-document extracts (``pages is None``) are unchanged by this module.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

from ocr_core import ocr_pages

# Align with ``_pdf_read`` usable-content floor (alnum ≥ 50 ⇒ keep text path).
_THIN_PAGE_ALNUM: Final[int] = 50


def page_needs_ocr(page: Any) -> bool:
    """True iff page text is thin/empty **and** the page embeds images."""
    text = page.get_text("text") or ""
    alnum = sum(1 for c in text if c.isalnum())
    if alnum >= _THIN_PAGE_ALNUM:
        return False
    images = page.get_images(full=True)
    return bool(images)


def classify_selected_pages(
    path: Path,
    pages: list[int],
) -> tuple[list[int], dict[int, str]]:
    """Split ``pages`` into OCR-needed numbers and text-rich ``{page: text}``.

    Page numbers are 1-based. Out-of-range pages are skipped.
    """
    import pymupdf  # type: ignore[import-untyped]

    ocr_pages_list: list[int] = []
    text_by_page: dict[int, str] = {}
    doc = pymupdf.open(str(path))
    try:
        page_count = len(doc)
        for page_num in pages:
            if page_num < 1 or page_num > page_count:
                continue
            page = doc[page_num - 1]
            if page_needs_ocr(page):
                ocr_pages_list.append(page_num)
            else:
                text_by_page[page_num] = page.get_text("text") or ""
    finally:
        doc.close()
    return ocr_pages_list, text_by_page


def extract_text_pdf_selected_pages(
    path: Path,
    pages: list[int],
    *,
    stargate_url: str,
    dpi: int,
    model: str,
    prompt: str,
) -> tuple[str, int | None]:
    """Extract only ``pages`` from a text-layer PDF with per-page OCR gate.

    - Text-rich selected pages → pymupdf text.
    - Thin/empty + images → ``ocr_pages`` for those page numbers only.
    - All selected pages OCR → one batched ``ocr_pages`` call.
    - Mixed → OCR the thin pages (batched), then merge in page order.
    """
    ocr_list, text_by_page = classify_selected_pages(path, pages)
    if not ocr_list and not text_by_page:
        return "", None

    if ocr_list and not text_by_page:
        return _run_ocr(
            path,
            ocr_list,
            stargate_url=stargate_url,
            dpi=dpi,
            model=model,
            prompt=prompt,
        )

    if not ocr_list:
        return _join_text_pages(pages, text_by_page), None

    ocr_text, tokens = _run_ocr(
        path,
        ocr_list,
        stargate_url=stargate_url,
        dpi=dpi,
        model=model,
        prompt=prompt,
    )
    ocr_by_page = _ocr_text_by_page(ocr_list, ocr_text)
    parts: list[str] = []
    for page_num in pages:
        if page_num in text_by_page:
            body = text_by_page[page_num].strip()
            if body:
                parts.append(f"\n--- Page {page_num} ---\n{body}")
        elif page_num in ocr_by_page:
            body = ocr_by_page[page_num].strip()
            if not body:
                continue
            if body.startswith("--- Page") or body.startswith("\n--- Page"):
                parts.append(body if body.startswith("\n") else f"\n{body}")
            else:
                parts.append(f"\n--- Page {page_num} ---\n{body}")
    return "\n".join(parts).strip(), tokens


def _run_ocr(
    path: Path,
    pages: list[int],
    *,
    stargate_url: str,
    dpi: int,
    model: str,
    prompt: str,
) -> tuple[str, int | None]:
    kwargs: dict[str, Any] = {
        "stargate_url": stargate_url,
        "dpi": dpi,
        "pages": pages,
    }
    if model:
        kwargs["model"] = model
    if prompt:
        kwargs["prompt"] = prompt
    result = ocr_pages(path, **kwargs)
    return result.get("text", "") or "", result.get("total_tokens")


def _join_text_pages(pages: list[int], text_by_page: dict[int, str]) -> str:
    parts: list[str] = []
    for page_num in pages:
        body = (text_by_page.get(page_num) or "").strip()
        if body:
            parts.append(f"\n--- Page {page_num} ---\n{body}")
    return "\n".join(parts).strip()


def _ocr_text_by_page(ocr_list: list[int], ocr_text: str) -> dict[int, str]:
    """Map OCR output onto page numbers.

    Single-page OCR returns a headed block; multi-page batches may return
    one combined string — attach the whole blob to the first OCR page and
    leave later OCR pages empty so mixed merges stay ordered.
    """
    if len(ocr_list) == 1:
        return {ocr_list[0]: ocr_text}
    # Prefer splitting on the headers ocr_pages emits for single-page batches;
    # when absent (multi-page batch), stash under the first page only.
    by_page: dict[int, str] = {n: "" for n in ocr_list}
    if "--- Page " in ocr_text:
        chunks = ocr_text.split("\n--- Page ")
        for chunk in chunks:
            if not chunk.strip():
                continue
            head, _, rest = chunk.partition(" ---\n")
            try:
                num = int(head.strip())
            except ValueError:
                continue
            if num in by_page:
                by_page[num] = f"\n--- Page {num} ---\n{rest}"
        return by_page
    by_page[ocr_list[0]] = ocr_text
    return by_page
