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
    if "overlapping pages differ" in text or "differ" in text:
        return refuse("harvest.page_overlap", text)
    if "gap" in text:
        return refuse("harvest.page_gap", text)
    return refuse(code, text)


def check_pages(
    pages: list[dict[str, Any]],
    *,
    prior_lines: list[str] | None,
    prior_total: int | None,
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
    covered: dict[int, str] = {}
    if prior_lines is not None:
        for offset, line in enumerate(prior_lines):
            covered[offset] = line
        prior_next = len(prior_lines)
    else:
        prior_next = None
    ordered = sorted(pages, key=lambda p: (p["lo"], p["hi"]))
    if prior_next is not None:
        earliest = ordered[0]["lo"]
        if earliest > prior_next:
            return refuse(
                "harvest.not_prefix",
                f"first page lo {earliest} > prior next {prior_next}",
            )
    kept_flags: list[bool] = []
    for page in ordered:
        lo, hi = page["lo"], page["hi"]
        if lo > hi:
            return refuse("harvest.page_header", f"page {lo}-{hi} is not 0-based")
        expected = hi - lo + 1
        got = len(page["lines"])
        if got != expected:
            return refuse(
                "harvest.page_header",
                f"page {lo}-{hi}: {got} lines, expected {expected}; re-page that range",
            )
        mismatch = False
        for offset, line in enumerate(page["lines"]):
            pos = lo + offset
            prior = covered.get(pos)
            if prior is not None and prior != line:
                mismatch = True
                break
        if mismatch:
            return refuse(
                "harvest.page_overlap", f"page {lo}-{hi} overlaps with different bytes"
            )
        fully = expected == got and all(lo + offset in covered for offset in range(got))
        kept_flags.append(not fully)
        if not fully:
            for offset, line in enumerate(page["lines"]):
                covered.setdefault(lo + offset, line)
    if not covered:
        return refuse("harvest.pages_missing", "no positions after stitch")
    start = 0 if prior_lines is not None else min(covered)
    end = max(covered)
    missing = [pos for pos in range(start, end + 1) if pos not in covered]
    if missing:
        return refuse(
            "harvest.page_gap", f"gap in positions: first missing {missing[0]}"
        )
    prior_next = len(prior_lines) if prior_lines is not None else 0
    if target_total - 1 not in covered or any(
        pos not in covered for pos in range(prior_next, target_total)
    ):
        return refuse(
            "harvest.page_gap",
            f"coverage must run {prior_next}..{target_total - 1}",
        )
    if prior_lines is not None and not any(pos >= prior_next for pos in covered):
        return refuse(
            "harvest.no_new_pages", "no page adds a position beyond the prior"
        )
    for page, keep in zip(ordered, kept_flags, strict=True):
        page["keep"] = keep
        page["target_total"] = target_total
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
