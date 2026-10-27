"""Stitch a `<n>.pages/` folder into verbatim raw bytes.

Raw bytes are never rewritten. Header totals are reconciled only in the
text passed to ``normalize_grokbot_l0_pages`` (newest page first, so the
existing first-header anchor is the folder N).
"""

from __future__ import annotations

import json
import re
from typing import Any

from continuity_tape.extract_grokbot_jsonl import (
    HEADER_RE,
    TranscriptPageError,
    _physical_lines,
    reassemble_pages,
    stitch_ordered_page_lines,
)

from ._clients import refuse

_PAGE_NAME = re.compile(r"^p(\d{6})-(\d{6})\.txt$")


def join_chunks(chunks: list[bytes]) -> bytes:
    """Concatenate chunks, inserting one newline only when the previous lacks one."""
    out = b""
    for chunk in chunks:
        if not chunk:
            continue
        if out and not out.endswith(b"\n"):
            out += b"\n"
        out += chunk
    return out


def _parse_page(text: str) -> dict[str, Any]:
    lines = _physical_lines(text)
    header_at: int | None = None
    header = None
    for index, line in enumerate(lines):
        match = HEADER_RE.match(line)
        if match:
            if header_at is not None:
                raise TranscriptPageError("page has a second header")
            header_at = index
            header = match
    if header is None or header_at is None:
        raise TranscriptPageError("no transcript page header")
    target = header.group("target").strip()
    lo, hi, total = int(header.group(2)), int(header.group(3)), int(header.group(4))
    body: list[str] = []
    for line in lines[header_at + 1 :]:
        if not line.strip():
            continue
        if line.strip().startswith("Older messages remain:"):
            continue
        if line.strip() == "This is the start of the transcript.":
            continue
        if line.strip().startswith("<cursor_untrusted_data_"):
            continue
        if line.strip().startswith("</cursor_untrusted_data_"):
            continue
        body.append(line.strip())
    return {
        "lo": lo,
        "hi": hi,
        "total": total,
        "target": target,
        "lines": body,
    }


def _mapped(code: str, exc: TranscriptPageError) -> dict[str, Any]:
    text = str(exc)
    if "second header" in text:
        return refuse("harvest.page_multi", text)
    if "no transcript" in text or "no transcript page" in text:
        return refuse("harvest.page_header", text)
    if "disagree on total" in text:
        return refuse("harvest.page_total", text)
    if "disagree on target" in text:
        return refuse("harvest.page_target", text)
    if "overlapping pages differ" in text or "ambiguous overlap" in text:
        return refuse("harvest.page_overlap", text)
    if "gap" in text:
        return refuse("harvest.page_gap", text)
    if "lines, expected" in text or "extend past" in text:
        return refuse("harvest.page_header", text)
    return refuse(code, text)


def check_pages(
    pages: list[dict[str, Any]],
    *,
    prior_lines: list[str] | None,
    prior_total: int | None,
    prior_next_pos: int | None = None,
) -> dict[str, Any] | None:
    """Return a refusal or None. Does not mutate page bytes."""
    if not pages:
        return refuse("harvest.pages_missing", "pages folder has no page files")
    max_hi = max(p["hi"] for p in pages)
    target_total = max(p["total"] for p in pages if p["hi"] == max_hi)
    if prior_total is not None and target_total < prior_total:
        return refuse(
            "harvest.page_total",
            f"folder N {target_total} < prior total {prior_total}",
        )
    for page in pages:
        if page["total"] < target_total or page["hi"] >= page["total"]:
            return refuse(
                "harvest.page_total",
                f"page {page['lo']}-{page['hi']} of {page['total']} "
                f"is below target {target_total}",
            )
    targets = {p["target"] for p in pages}
    if len(targets) != 1:
        return refuse(
            "harvest.page_target", f"pages disagree on target: {sorted(targets)}"
        )
    ordered = sorted(pages, key=lambda p: (p["lo"], p["hi"]))
    prior_next: int | None = None
    if prior_lines is not None:
        prior_next = (
            prior_next_pos
            if prior_next_pos is not None
            else len(prior_lines)
        )
        earliest = ordered[0]["lo"]
        if earliest > prior_next:
            return refuse(
                "harvest.not_prefix",
                f"first page lo {earliest} > prior next {prior_next}",
            )
    for page in ordered:
        lo, hi = page["lo"], page["hi"]
        if lo > hi:
            return refuse("harvest.page_header", f"page {lo}-{hi} is not 0-based")
        expected = hi - lo + 1
        got = len(page["lines"])
        if got > expected:
            return refuse(
                "harvest.page_header",
                f"page {lo}-{hi}: {got} lines, expected {expected}",
            )
        page["unrendered"] = expected - got
    tuples = [(p["lo"], p["hi"], p["lines"]) for p in ordered]
    prior_running_hi = (prior_next - 1) if prior_next is not None else None
    try:
        stitched = stitch_ordered_page_lines(
            tuples,
            prior_lines=prior_lines,
            prior_running_hi=prior_running_hi,
        )
    except TranscriptPageError as exc:
        return _mapped("harvest.page_overlap", exc)
    if not stitched.lines:
        return refuse("harvest.pages_missing", "no lines after stitch")
    if stitched.running_hi < target_total - 1:
        return refuse(
            "harvest.page_gap",
            f"coverage must reach position {target_total - 1}, got hi {stitched.running_hi}",
        )
    if prior_lines is not None and not any(stitched.page_added):
        return refuse(
            "harvest.no_new_pages", "no page adds a line beyond the prior"
        )
    total_unrendered = stitched.unrendered_positions
    for page, keep in zip(ordered, stitched.page_added, strict=True):
        page["keep"] = keep
        page["target_total"] = target_total
        page["total_unrendered"] = total_unrendered
    return None


def raw_bytes_for(
    prior: bytes,
    ordered_files: list[tuple[dict[str, Any], bytes]],
) -> bytes:
    chunks: list[bytes] = []
    if prior:
        chunks.append(prior)
    for page, blob in sorted(
        ordered_files, key=lambda item: (item[0]["lo"], item[0]["hi"])
    ):
        if page.get("keep", True):
            chunks.append(blob)
    return join_chunks(chunks)


def normalize_input(prior_text: str, kept: list[tuple[dict[str, Any], str]]) -> str:
    """Newest-first copy so normalize anchors on the folder N. Raw is not this string."""
    newest = sorted(kept, key=lambda item: (item[0]["lo"], item[0]["hi"]), reverse=True)
    parts = [text for _page, text in newest]
    if prior_text:
        parts.append(prior_text)
    return join_chunks([p.encode("utf-8") for p in parts]).decode("utf-8")


def prior_window(messages_blob: bytes) -> tuple[int, int] | None:
    """Return (next 0-based position, total) from a sealed messages file."""
    payload = json.loads(messages_blob.decode("utf-8"))
    sources = (payload.get("meta") or {}).get("sources") or []
    window = (sources[0] or {}).get("window") if sources else None
    if not isinstance(window, dict):
        return None
    hi = window.get("hi")
    total = window.get("total")
    if not isinstance(hi, int) or not isinstance(total, int):
        return None
    return hi, total


def load_prior_lines(raw: bytes) -> list[str]:
    lines, _info = reassemble_pages(raw.decode("utf-8"))
    return lines


__all__ = [
    "HEADER_RE",
    "check_pages",
    "join_chunks",
    "load_prior_lines",
    "normalize_input",
    "prior_window",
    "raw_bytes_for",
    "_mapped",
    "_parse_page",
    "_PAGE_NAME",
]
