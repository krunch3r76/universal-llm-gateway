"""Dropbox PDF selection and routing (plan D3)."""

from __future__ import annotations

import fnmatch
import re
from datetime import date

from ._extract_document_helpers import detect_format
from ._extract_document_page_gate import page_needs_ocr
from ._file_helpers import FILES_ROOT, resolve_files_path

_DROPBOX_RE = re.compile(
    r"^dropbox/(?P<ctx>[^/]+)/(?P<folder>\d{4}-\d{2}-\d{2})/(?P<file>[^/]+\.[Pp][Dd][Ff])$",
)


def model_family(model: str, family_map: list[tuple[str, str]]) -> str | None:
    for pattern, family in family_map:
        if fnmatch.fnmatch(model, pattern):
            return family
    return None


def parse_dropbox_rel(rel: str) -> tuple[str, str, str] | None:
    match = _DROPBOX_RE.match(rel.strip("/"))
    if not match:
        return None
    return match.group("ctx"), match.group("folder"), match.group("file")


def _folder_ok(folder: str, since: str) -> bool:
    try:
        return date.fromisoformat(folder) >= date.fromisoformat(since)
    except ValueError:
        return False


def discover_dropbox_pdfs(ctx: str | None = None) -> list[str]:
    root = FILES_ROOT / "dropbox"
    if not root.is_dir():
        return []
    paths: list[str] = []
    ctx_dirs = [root / ctx] if ctx else sorted(p for p in root.iterdir() if p.is_dir())
    for ctx_dir in ctx_dirs:
        if not ctx_dir.is_dir():
            continue
        for date_dir in sorted(ctx_dir.iterdir()):
            if not date_dir.is_dir():
                continue
            for pdf in sorted(date_dir.iterdir()):
                if pdf.is_file() and pdf.suffix.lower() == ".pdf":
                    rel = pdf.relative_to(FILES_ROOT).as_posix()
                    paths.append(rel)
    return paths


def select_paths(
    *,
    paths: list[str] | None,
    ctx: str | None,
    since: str,
    cap: int,
    ctx_allowlist: set[str],
    skip_predicate,
) -> list[str]:
    """Return ordered rel paths respecting shape, allowlist, since, and cap."""
    if paths is not None:
        candidates = []
        for rel in paths:
            parsed = parse_dropbox_rel(rel)
            if parsed is None:
                continue
            p_ctx, _folder, _file = parsed
            if p_ctx not in ctx_allowlist:
                continue
            candidates.append(rel)
    else:
        raw = discover_dropbox_pdfs(ctx)
        candidates = []
        for rel in raw:
            parsed = parse_dropbox_rel(rel)
            if parsed is None:
                continue
            p_ctx, folder, _file = parsed
            if p_ctx not in ctx_allowlist:
                continue
            if not _folder_ok(folder, since):
                continue
            candidates.append(rel)

    candidates.sort(key=lambda p: (parse_dropbox_rel(p) or ("", "", p))[1:3])
    selected: list[str] = []
    budget = cap
    for rel in candidates:
        if budget <= 0:
            break
        if skip_predicate(rel):
            continue
        selected.append(rel)
        budget -= 1
    return selected


def route_pdf(rel: str) -> tuple[str | None, str | None]:
    """Return (method, terminal_last_error)."""
    import pymupdf  # type: ignore[import-untyped]

    abs_path = resolve_files_path(rel, root=FILES_ROOT)
    fmt = detect_format(abs_path)
    if fmt == "text_pdf":
        doc = pymupdf.open(str(abs_path))
        try:
            for page in doc:
                if page_needs_ocr(page):
                    return None, "mixed_text_layer"
        finally:
            doc.close()
        return "text_layer", None
    if fmt == "scanned_pdf":
        return "vision_crosscheck", None
    return None, "unknown_format"
