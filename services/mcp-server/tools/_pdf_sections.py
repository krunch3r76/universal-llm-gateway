"""TOC-driven section navigation for PDFs in the fs markdown tool.

PDF section titles frequently do not convert to ATX (``#``) markdown, so the
ATX splitter in ``markdown_sections`` returns a single ``[Preamble]`` spanning
the whole document and ``md_read`` by section is unusable. When a PDF carries
an embedded outline (``doc.get_toc(simple=False)``), that outline is the
document's authoritative section structure — and it ships destination
coordinates, so two outline entries on the same page get precise boundaries
without fragile heading-text matching.

This module builds section records from the outline and extracts per-section
text by clipping page regions between consecutive anchors. PDFs without an
outline fall back to per-page sections so agents get a bounded read instead of
a whole-file dump. The boundary fidelity is reported honestly via
``boundary_precision`` (``coordinate`` for TOC anchors, ``page`` for the
page fallback) — a PDF section read is a coordinate-clipped region, not an
exact markdown slice.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pymupdf  # type: ignore[import-untyped]

SOURCE_PDF_TOC = "pdf_toc"
SOURCE_PDF_PAGE = "pdf_page_fallback"
PRECISION_COORDINATE = "coordinate"
PRECISION_PAGE = "page"


class PdfSectionError(Exception):
    """Invalid or ambiguous PDF section selector."""


@dataclass(slots=True, kw_only=True)
class PdfSection:
    """A PDF section spanning ``[start .. end)`` in (page, top-left y) space.

    ``start_y`` / ``end_y`` are top-left page coordinates (``None`` means the
    page edge). Pages are 0-based internally; metadata rows expose 1-based.
    """

    heading: str
    level: int
    path: str
    start_page: int
    start_y: float | None
    end_page: int
    end_y: float | None
    source: str
    boundary_precision: str


def _toc_anchors(doc: pymupdf.Document) -> list[tuple[int, str, int, float]]:
    """Return ``(level, title, page0, top_y)`` per outline entry, ordered.

    The destination ``y`` from ``get_toc`` is in PDF user space (bottom-left
    origin); convert to the top-left origin used by ``page.get_text(clip=...)``.
    Entries whose page is out of range or carry no point are skipped.
    """
    anchors: list[tuple[int, str, int, float]] = []
    for entry in doc.get_toc(simple=False):
        level, title, page_1based = entry[0], entry[1], entry[2]
        dest = entry[3] if len(entry) > 3 else None
        page0 = page_1based - 1
        if page0 < 0 or page0 >= doc.page_count:
            continue
        point = dest.get("to") if isinstance(dest, dict) else None
        if point is None:
            top_y = 0.0
        else:
            top_y = max(0.0, doc[page0].rect.height - float(point.y))
        anchors.append((level, str(title).strip(), page0, top_y))
    return anchors


def _build_path(stack: list[tuple[int, str]], level: int, heading: str) -> str:
    """Hierarchical path from the heading stack (mirrors markdown_sections)."""
    while stack and stack[-1][0] >= level:
        stack.pop()
    esc = heading.replace("/", "\\/")
    path = f"{'/'.join(s for _, s in stack)}/{esc}" if stack else esc
    stack.append((level, esc))
    return path


def _toc_sections(doc: pymupdf.Document) -> list[PdfSection]:
    """Build sections from the outline; end of each span is the next anchor."""
    anchors = _toc_anchors(doc)
    if not anchors:
        return []
    last_page = doc.page_count - 1
    sections: list[PdfSection] = []
    stack: list[tuple[int, str]] = []
    for i, (level, heading, page0, top_y) in enumerate(anchors):
        if i + 1 < len(anchors):
            _, _, next_page, next_y = anchors[i + 1]
            end_page, end_y = next_page, next_y
        else:
            end_page, end_y = last_page, None
        sections.append(
            PdfSection(
                heading=heading,
                level=level,
                path=_build_path(stack, level, heading),
                start_page=page0,
                start_y=top_y,
                end_page=end_page,
                end_y=end_y,
                source=SOURCE_PDF_TOC,
                boundary_precision=PRECISION_COORDINATE,
            )
        )
    return sections


def _page_sections(doc: pymupdf.Document) -> list[PdfSection]:
    """One section per page — bounded-read fallback for outline-less PDFs."""
    return [
        PdfSection(
            heading=f"Page {p + 1}",
            level=1,
            path=f"Page {p + 1}",
            start_page=p,
            start_y=None,
            end_page=p,
            end_y=None,
            source=SOURCE_PDF_PAGE,
            boundary_precision=PRECISION_PAGE,
        )
        for p in range(doc.page_count)
    ]


def build_sections(doc: pymupdf.Document) -> list[PdfSection]:
    """Outline-driven sections when available, else per-page fallback."""
    return _toc_sections(doc) or _page_sections(doc)


def _section_text(doc: pymupdf.Document, sec: PdfSection) -> str:
    """Extract text for ``sec`` by clipping each spanned page between anchors."""
    parts: list[str] = []
    for page0 in range(sec.start_page, sec.end_page + 1):
        page = doc[page0]
        height = page.rect.height
        top = sec.start_y if page0 == sec.start_page and sec.start_y else 0.0
        if page0 == sec.end_page and sec.end_y is not None:
            bottom = sec.end_y
        else:
            bottom = height
        if bottom <= top:
            # Degenerate same-page span (zero/negative height) — take to page end.
            bottom = height
        clip = pymupdf.Rect(0.0, top, page.rect.width, bottom)
        parts.append(page.get_text("text", clip=clip, sort=True))
    return "".join(parts)


def _resolve(sections: list[PdfSection], selector: str) -> PdfSection:
    """Resolve by full path, exact heading, or bare-leaf suffix."""
    normalized = selector.strip()
    if normalized == "":
        raise PdfSectionError(
            "PDF sections have no preamble; pass a section heading or page label"
        )
    exact = [s for s in sections if s.path == normalized]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise PdfSectionError(f"Multiple sections match path {selector!r}")
    unesc = normalized.replace("\\/", "/")
    by_heading = [s for s in sections if s.heading == unesc]
    if len(by_heading) == 1:
        return by_heading[0]
    if len(by_heading) > 1:
        raise PdfSectionError(
            f"Ambiguous heading {selector!r}. "
            f"Full paths: {', '.join(repr(s.path) for s in by_heading)}"
        )
    if "/" not in normalized:
        suffix = [s for s in sections if s.path.endswith(f"/{normalized}")]
        if len(suffix) == 1:
            return suffix[0]
        if len(suffix) > 1:
            raise PdfSectionError(
                f"Ambiguous section {selector!r}. "
                f"Full paths: {', '.join(repr(s.path) for s in suffix)}"
            )
    raise PdfSectionError(f"Section not found: {selector!r}")


def _row(sec: PdfSection, chars: int) -> dict[str, object]:
    return {
        "heading": sec.heading,
        "level": sec.level,
        "path": sec.path,
        "chars": chars,
        "start_page": sec.start_page + 1,
        "end_page": sec.end_page + 1,
        "source": sec.source,
        "boundary_precision": sec.boundary_precision,
    }


def list_pdf_sections(path: Path) -> dict[str, object]:
    """Section metadata for a PDF, outline-driven with per-page fallback."""
    doc = pymupdf.open(str(path))
    try:
        sections = build_sections(doc)
        rows = [_row(sec, len(_section_text(doc, sec))) for sec in sections]
        total = sum(int(r["chars"]) for r in rows)
    finally:
        doc.close()
    source = sections[0].source if sections else SOURCE_PDF_PAGE
    return {"sections": rows, "total_chars": total, "source": source}


def read_pdf_section(path: Path, selector: str) -> str:
    """Return the clipped text of one PDF section (raises PdfSectionError)."""
    doc = pymupdf.open(str(path))
    try:
        sec = _resolve(build_sections(doc), selector)
        return _section_text(doc, sec)
    finally:
        doc.close()


def _nest(
    sections: list[PdfSection], texts: list[str], start: int, end: int
) -> dict[str, object]:
    """Fold a flat section span into a nested dict keyed by heading.

    Each section's own clipped text is its *direct* body — the outline span
    runs to the next anchor of any level, so a parent's text already excludes
    its children. Parents with a body carry it under ``_content`` (mirrors
    ``markdown_sections.sections_to_dict``).
    """
    target: dict[str, object] = {}
    i = start
    while i < end:
        sec, body = sections[i], texts[i]
        child_end = i + 1
        while child_end < end and sections[child_end].level > sec.level:
            child_end += 1
        if child_end == i + 1:
            target[sec.heading] = body
        else:
            child: dict[str, object] = {}
            if body.strip():
                child["_content"] = body
            child.update(_nest(sections, texts, i + 1, child_end))
            target[sec.heading] = child
        i = child_end
    return target


def pdf_to_dict(path: Path) -> dict[str, object]:
    """Nested heading→body dict for a PDF, outline-driven with page fallback."""
    doc = pymupdf.open(str(path))
    try:
        sections = build_sections(doc)
        texts = [_section_text(doc, sec) for sec in sections]
    finally:
        doc.close()
    return _nest(sections, texts, 0, len(sections))


def _outline_descriptors(listing: dict[str, object]) -> tuple[list[dict[str, object]], int]:
    """TOC rows with the same ids ``read_pdf_section`` resolves, plus child metadata."""
    rows = list(listing.get("sections") or [])
    stack: list[tuple[int, str]] = []
    raw: list[dict[str, object]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        level = int(row.get("level") or 1)
        sid = str(row.get("path") or row.get("heading") or "")
        while stack and stack[-1][0] >= level:
            stack.pop()
        parent_id = stack[-1][1] if stack else None
        raw.append(
            {
                "id": sid,
                "heading": str(row.get("heading") or ""),
                "level": level,
                "parent_id": parent_id,
                "char_count": int(row.get("chars") or 0),
            }
        )
        stack.append((level, sid))
    children: dict[str, list[str]] = {str(item["id"]): [] for item in raw}
    for item in raw:
        parent = item["parent_id"]
        if isinstance(parent, str) and parent in children:
            children[parent].append(str(item["id"]))
    by_id = {str(item["id"]): item for item in raw}

    def _subtree(sid: str) -> int:
        own = int(by_id[sid]["char_count"])
        return own + sum(_subtree(child) for child in children.get(sid, []))

    descriptors: list[dict[str, object]] = []
    for item in raw:
        sid = str(item["id"])
        descriptors.append(
            {
                **item,
                "children": children.get(sid, []),
                "subtree_char_count": _subtree(sid),
            }
        )
    total = listing.get("total_chars")
    total_chars = int(total) if isinstance(total, int) else sum(
        int(item["char_count"]) for item in raw
    )
    return descriptors, total_chars


def _match_pdf_descriptor(
    descriptors: list[dict[str, object]], section_id: str
) -> tuple[dict[str, object] | None, str]:
    exact = [row for row in descriptors if row.get("id") == section_id]
    if len(exact) == 1:
        return exact[0], "ok"
    if len(exact) > 1:
        return None, "duplicate_ambiguous"
    heading = [
        row
        for row in descriptors
        if row.get("heading") == section_id or str(row.get("id") or "").endswith("/" + section_id)
    ]
    if len(heading) == 1:
        return heading[0], "ok"
    if len(heading) > 1:
        return None, "duplicate_ambiguous"
    return None, "missing"


def resolve_pdf_outline_read(
    *,
    file_path: Path,
    path: str,
    sandbox: str,
    read_sha256: str,
    section: str | None = None,
    sections: list[str] | None = None,
    chunk_cursor: int | None = None,
    chunk_cursor_provided: bool = False,
    expected_read_sha256: str | None = None,
) -> dict[str, object]:
    """PDF manifest and section expansion in the outline id space.

    ``sections`` / ``export`` must not re-parse extracted markdown headings:
    those ids do not match the TOC manifest.
    """
    from tools._hashing import sha256_hex_equal, sha256_hex_of_bytes

    from ._sparse_document_read import (
        BATCH_MAX_CONTENT_CHARS,
        BATCH_MAX_SECTIONS,
        _chunk_cursor_out_of_range,
        _page_descriptors,
        finalize_sparse_document_payload,
        param_not_applicable_error,
        sparse_selector_conflict_error,
    )

    has_section = bool(section and str(section).strip())
    has_sections = bool(sections)
    if chunk_cursor_provided and has_sections:
        return sparse_selector_conflict_error(
            "chunk_cursor is not valid together with sections="
        )
    if chunk_cursor_provided and not (has_section and not has_sections):
        return param_not_applicable_error(parameter="chunk_cursor")
    if has_section and has_sections:
        return sparse_selector_conflict_error("Pass either section or sections, not both")
    if sections is not None and len(sections) == 0:
        return sparse_selector_conflict_error(
            "sections must be a non-empty list when provided"
        )
    if expected_read_sha256 and not sha256_hex_equal(read_sha256, expected_read_sha256):
        return finalize_sparse_document_payload(
            {
                "ok": False,
                "outcome": "stale_handle",
                "path": path,
                "read_sha256": read_sha256,
                "expected_read_sha256": expected_read_sha256,
                "selection": "section" if has_section or has_sections else "manifest",
                "omitted": ["content"],
            }
        )

    listing = list_pdf_sections(file_path)
    descriptors, total_chars = _outline_descriptors(listing)

    def _span(row: dict[str, object], body: str) -> dict[str, object]:
        kids = row.get("children")
        return {
            "span": "own_body",
            "includes_children": False,
            "children": list(kids) if isinstance(kids, list) else [],
            "char_count": len(body),
            "subtree_char_count": int(row.get("subtree_char_count") or len(body)),
        }

    def _load(row: dict[str, object]) -> str:
        return read_pdf_section(file_path, str(row["id"]))

    if not has_section and not has_sections:
        paged, omitted, cursor, truncated = _page_descriptors(descriptors)
        return finalize_sparse_document_payload(
            {
                "path": path,
                "sandbox": sandbox,
                "read_sha256": read_sha256,
                "total_chars": total_chars,
                "selection": "manifest",
                "structure": "sectioned" if descriptors else "unsectioned",
                "preamble": False,
                "sections": paged,
                "omitted": ["content", *omitted],
                "cursor": cursor,
                "truncated": truncated,
                "source": listing.get("source"),
            }
        )

    wanted = [str(section).strip()] if has_section else list(sections or [])
    if has_section:
        row, status = _match_pdf_descriptor(descriptors, wanted[0])
        if row is None:
            return finalize_sparse_document_payload(
                {
                    "ok": False,
                    "outcome": "missing" if status == "missing" else status,
                    "selection": "section",
                    "section": wanted[0],
                    "path": path,
                    "sandbox": sandbox,
                    "read_sha256": read_sha256,
                }
            )
        try:
            body = _load(row)
        except PdfSectionError as exc:
            return {"ok": False, "outcome": "missing", "error": str(exc), "selection": "section", "section": wanted[0], "path": path, "read_sha256": read_sha256}
        span = _span(row, body)
        if chunk_cursor_provided:
            start = 0 if chunk_cursor is None else int(chunk_cursor)
            if start < 0 or start > len(body):
                return _chunk_cursor_out_of_range(section_label=str(row["id"]))
            chunk = body[start : start + BATCH_MAX_CONTENT_CHARS]
            next_pos = start + len(chunk)
            chunk_next = next_pos if next_pos < len(body) else None
            return finalize_sparse_document_payload(
                {
                    "ok": True,
                    "outcome": "ok",
                    "content": chunk,
                    "path": path,
                    "sandbox": sandbox,
                    "section": str(row["id"]),
                    "read_sha256": read_sha256,
                    "content_sha256": sha256_hex_of_bytes(chunk.encode("utf-8")),
                    "chunk_cursor": start,
                    "chunk_cursor_next": chunk_next,
                    "truncated": chunk_next is not None,
                    "selection": "section",
                    **span,
                }
            )
        if len(body) > BATCH_MAX_CONTENT_CHARS:
            return finalize_sparse_document_payload(
                {
                    "ok": False,
                    "outcome": "over_budget",
                    "oversize": True,
                    "selection": "section",
                    "section": str(row["id"]),
                    "path": path,
                    "read_sha256": read_sha256,
                    "char_count": len(body),
                    "fetch": {"section": str(row["id"]), "chunk_cursor": 0},
                    **span,
                }
            )
        return finalize_sparse_document_payload(
            {
                "ok": True,
                "outcome": "ok",
                "content": body,
                "path": path,
                "sandbox": sandbox,
                "section": str(row["id"]),
                "read_sha256": read_sha256,
                "content_sha256": sha256_hex_of_bytes(body.encode("utf-8")),
                "selection": "section",
                **span,
            }
        )

    items: list[dict[str, object]] = []
    omitted: list[str] = []
    budget_cursor: int | None = None
    used = 0
    ended = False
    for idx, sid in enumerate(wanted):
        row, status = _match_pdf_descriptor(descriptors, sid)
        if ended or idx >= BATCH_MAX_SECTIONS:
            items.append({"id": sid, "outcome": "over_budget"})
            omitted.append(sid)
            if budget_cursor is None:
                budget_cursor = idx
            continue
        if row is None:
            items.append({"id": sid, "outcome": "missing" if status == "missing" else status})
            continue
        try:
            body = _load(row)
        except PdfSectionError:
            items.append({"id": sid, "outcome": "missing"})
            continue
        span = _span(row, body)
        if len(body) > BATCH_MAX_CONTENT_CHARS or used + len(body) > BATCH_MAX_CONTENT_CHARS:
            ended = True
            budget_cursor = idx
            items.append(
                {
                    "id": sid,
                    "outcome": "over_budget",
                    "oversize": len(body) > BATCH_MAX_CONTENT_CHARS,
                    "fetch": {"section": str(row["id"]), "chunk_cursor": 0},
                    **span,
                }
            )
            omitted.append(sid)
            continue
        used += len(body)
        items.append(
            {
                "id": sid,
                "outcome": "ok",
                "content": body,
                "content_sha256": sha256_hex_of_bytes(body.encode("utf-8")),
                **span,
            }
        )
    payload: dict[str, object] = {
        "path": path,
        "sandbox": sandbox,
        "read_sha256": read_sha256,
        "items": items,
        "selection": "sections",
        "sparse_section_batch": True,
        "source": listing.get("source"),
    }
    if omitted:
        payload["omitted"] = omitted
    if budget_cursor is not None:
        payload["budget_cursor"] = budget_cursor
    return finalize_sparse_document_payload(payload)
