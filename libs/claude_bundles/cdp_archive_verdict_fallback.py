"""Archive-body verdict when skill_receipt_unverified would fail-closed (a:27142).

Archive prose never promotes gate authority: a failed harvest stays failed even
when its archive carries an advancing verdict. Verdict extraction routes through
``review_verdict.grammar`` first and the legacy archive regex second, so every
body that parsed on master 371aa856 parses to the same token here.
"""

from __future__ import annotations

import logging
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

from review_verdict.grammar import (
    has_parseable_verdict,
    normalize_verdict_token,
    parse_any_review_body,
)

from claude_bundles.cdp_model_endpoint import CdpGenerateResult

logger = logging.getLogger(__name__)

_SKILL_RECEIPT = "skill_receipt_unverified"
_CANONICAL_VERDICT_LINE_RE = re.compile(
    r"(?m)^VERDICT:\s*(?P<raw>.+?)\s*$",
    re.IGNORECASE,
)
# Legacy archive spellings (``Verdict: approve``, ``r_verdict: ship``) kept for
# bodies the shared grammar does not tokenize; parity with master 371aa856.
_ARCHIVE_VERDICT_RE = re.compile(
    r"(?im)^(?:verdict|r[_-]?verdict|merits)\s*[:=]\s*"
    r"(approve|request_changes|pass|fail|ship|ratify)\b"
)


def _is_receipt_only_error(error: str | None) -> bool:
    """True when failure is solely skill receipt unverified (no compound tail)."""
    if not error:
        return False
    err = error.strip()
    if not err.startswith(f"{_SKILL_RECEIPT}:"):
        return False
    return ";" not in err


def _token_from_verdict_line_raw(raw: str) -> str | None:
    """First token on the canonical ``VERDICT:`` line; None for template echoes."""
    stripped = (raw or "").strip()
    if not stripped:
        return None
    if "<" in stripped and "|" in stripped:
        return None
    first = stripped.split()[0]
    if "|" in first:
        return None
    return first.upper().replace("-", "_")


def parse_canonical_verdict_line(body: str) -> str | None:
    """Return the first ``^VERDICT:`` line token, or None (template echoes refused)."""
    match = _CANONICAL_VERDICT_LINE_RE.search(body)
    if not match:
        return None
    return _token_from_verdict_line_raw(match.group("raw"))


def parse_archive_verdict(body: str) -> str | None:
    """Return a normalized verdict token from archive prose, or None.

    Order: shared ``review_verdict.grammar`` (Merits, gate-6 block, standalone
    ``VERDICT:`` line), then the legacy archive regex. The first word of the
    grammar token is normalized, so ``VERDICT: APPROVE range=a..b`` yields
    ``APPROVE`` and ``Verdict: approve`` yields ``APPROVE``. Same behavior as
    master 371aa856, including its quirk that a pipe-joined template line
    yields the joined token rather than None.
    """
    if has_parseable_verdict(body):
        parsed = parse_any_review_body(body)
        if parsed.token:
            head = str(parsed.token).strip().split()[0]
            return normalize_verdict_token(head)
    match = _ARCHIVE_VERDICT_RE.search(body)
    if match:
        return match.group(1).upper().replace("-", "_")
    return None


def _read_archive_text(archive_uri: str | None) -> str | None:
    if not archive_uri:
        return None
    uri = archive_uri.strip()
    try:
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
    except OSError:
        logger.debug("archive read failed for %s", archive_uri, exc_info=True)
        return None
    except Exception:
        logger.debug("archive read error for %s", archive_uri, exc_info=True)
        return None
    return None


def _salvage_archive_verdict_on_result(
    result: CdpGenerateResult,
) -> CdpGenerateResult:
    """Record the archive verdict on a receipt-only failure without promoting it.

    Applies only when ``result`` failed solely on ``skill_receipt_unverified``
    and its archive yields a token via :func:`parse_archive_verdict` (shared
    grammar, then legacy regex). The returned copy carries ``archive_verdict``
    and ``verdict_source="archive"`` in ``extras`` as non-authoritative
    evidence; ``ok``, ``error``, ``stall_stage`` and ``body`` are unchanged,
    because archive prose does not promote gate authority (Decision 06).
    Every other input is returned as the same object.
    """
    if result.ok or not _is_receipt_only_error(result.error):
        return result
    text = _read_archive_text(result.archive_uri)
    if not text:
        return result
    verdict = parse_archive_verdict(text)
    if not verdict:
        return result
    extras = dict(result.extras or {})
    extras["verdict_source"] = "archive"
    extras["archive_verdict"] = verdict
    return replace(result, extras=extras)


def apply_archive_verdict_fallback(result: CdpGenerateResult) -> CdpGenerateResult:
    """Return ``result`` unchanged — archive prose does not promote gate authority."""
    return result


def _apply_archive_verdict_fallback_terminal_payload(
    payload: dict[str, Any],
    *,
    execution_id: str,
    purpose: str | None,
) -> dict[str, Any]:
    """Return ``payload`` unchanged for every purpose (Decision 06: no promotion).

    A failed ``skill_receipt_unverified`` harvest stays failed even when its
    archive carries ``VERDICT: APPROVE`` or ``APPROVE_WITH_NITS``.
    """
    del execution_id, purpose
    return payload


def apply_archive_verdict_fallback_terminal_payload(
    payload: dict[str, Any],
    *,
    execution_id: str,
    purpose: str | None = None,
) -> dict[str, Any]:
    """Archive-verdict hook on a cdp_ask ``finish_execution`` payload.

    Never promotes: returns ``payload`` unchanged, including when an internal
    error is raised (logged, then the original payload is returned).
    """
    try:
        return _apply_archive_verdict_fallback_terminal_payload(
            payload, execution_id=execution_id, purpose=purpose
        )
    except Exception:
        logger.exception(
            "archive verdict fallback failed execution_id=%s", execution_id
        )
        return payload


__all__ = [
    "apply_archive_verdict_fallback",
    "apply_archive_verdict_fallback_terminal_payload",
    "parse_archive_verdict",
    "parse_canonical_verdict_line",
]
