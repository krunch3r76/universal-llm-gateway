"""Archive-body verdict when skill_receipt_unverified would fail-closed (a:27142)."""

from __future__ import annotations

import re
from pathlib import Path

from review_verdict.grammar import (
    has_parseable_verdict,
    normalize_verdict_token,
    parse_any_review_body,
)

from claude_bundles.cdp_model_endpoint import CdpGenerateResult

_SKILL_RECEIPT = "skill_receipt_unverified"
_ARCHIVE_VERDICT_RE = re.compile(
    r"(?im)^(?:verdict|r[_-]?verdict|merits)\s*[:=]\s*"
    r"(approve|request_changes|pass|fail|ship|ratify)\b"
)


def _read_archive_text(archive_uri: str | None) -> str | None:
    if not archive_uri:
        return None
    uri = archive_uri.strip()
    if uri.startswith("cortex://"):
        rel = uri.removeprefix("cortex://")
        from cortex_store.files_path_normalize import normalize_cortex_files_path

        path = normalize_cortex_files_path(rel)
        if not path.is_file():
            return None
        return path.read_text(encoding="utf-8")
    if uri.startswith("file://"):
        path = Path(uri.removeprefix("file://"))
        if path.is_file():
            return path.read_text(encoding="utf-8")
    return None


def parse_archive_verdict(body: str) -> str | None:
    """Return normalized verdict token from archive prose, or None."""
    if has_parseable_verdict(body):
        parsed = parse_any_review_body(body)
        if parsed.token:
            head = str(parsed.token).strip().split()[0]
            return normalize_verdict_token(head)
    match = _ARCHIVE_VERDICT_RE.search(body)
    if match:
        return match.group(1).upper().replace("-", "_")
    return None


def apply_archive_verdict_fallback(result: CdpGenerateResult) -> CdpGenerateResult:
    """Return ``result`` unchanged — archive prose does not promote gate authority."""
    return result


__all__ = ["apply_archive_verdict_fallback", "parse_archive_verdict"]
