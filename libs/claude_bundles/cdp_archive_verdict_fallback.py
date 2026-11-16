"""Archive-body verdict when skill_receipt_unverified would fail-closed (a:27142)."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from claude_bundles.cdp_model_endpoint import CdpGenerateResult

logger = logging.getLogger(__name__)

_SKILL_RECEIPT = "skill_receipt_unverified"
_CANONICAL_VERDICT_LINE_RE = re.compile(
    r"(?m)^VERDICT:\s*(?P<raw>.+?)\s*$",
    re.IGNORECASE,
)
_ADVANCE_VERDICTS = frozenset({"APPROVE", "APPROVE_WITH_NITS"})


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
    """Return the first ``^VERDICT:`` line token, or None."""
    match = _CANONICAL_VERDICT_LINE_RE.search(body)
    if not match:
        return None
    return _token_from_verdict_line_raw(match.group("raw"))


def parse_archive_verdict(body: str) -> str | None:
    """Return canonical ``VERDICT:`` token from archive prose (promotion parser only)."""
    return parse_canonical_verdict_line(body)


def _verdict_advances(token: str | None) -> bool:
    return token in _ADVANCE_VERDICTS if token else False


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
    """When skill_receipt_unverified, prefer a canonical archive ``VERDICT:`` line."""
    if not _is_receipt_only_error(result.error):
        return result
    if result.ok:
        return result
    text = _read_archive_text(result.archive_uri)
    if not text:
        return result
    verdict = parse_canonical_verdict_line(text)
    if not verdict:
        return result
    advance = _verdict_advances(verdict)
    if not advance:
        return result
    preserved_err = result.error
    extras = dict(result.extras or {})
    extras["verdict_source"] = "archive"
    extras["archive_verdict"] = verdict
    if preserved_err:
        extras["skill_receipt_warning"] = preserved_err
        warnings = list(extras.get("warnings") or [])
        if preserved_err not in warnings:
            warnings.append(preserved_err)
        extras["warnings"] = warnings
    return CdpGenerateResult(
        ok=True,
        body=result.body or text,
        execution_id=result.execution_id,
        satellite_execution_id=result.satellite_execution_id,
        prompt_uri=result.prompt_uri,
        picker_model=result.picker_model,
        archive_uri=result.archive_uri,
        content_proof_uri=result.content_proof_uri,
        content_proof_sha256=result.content_proof_sha256,
        stall_stage=None,
        error=None,
        substrate=result.substrate,
        cost_source=result.cost_source,
        poll_snapshots=result.poll_snapshots,
        extras=extras,
    )


def apply_archive_verdict_fallback(result: CdpGenerateResult) -> CdpGenerateResult:
    """Return ``result`` unchanged — archive prose does not promote gate authority."""
    return result


def _payload_archive_uri(payload: dict[str, Any]) -> str | None:
    uri = payload.get("archive_uri")
    if uri:
        return str(uri)
    results = payload.get("results")
    if isinstance(results, list) and results:
        last = results[-1]
        if isinstance(last, dict) and last.get("archive_uri"):
            return str(last["archive_uri"])
    return None


def _promotion_allowed(*, purpose: str | None) -> bool:
    """Receipt salvage is limited to delivery-review harvests."""
    return (purpose or "").strip().lower() == "review"


def _merge_promoted_payload(
    payload: dict[str, Any],
    out: CdpGenerateResult,
    *,
    preserved_err: str | None,
) -> dict[str, Any]:
    merged = dict(payload)
    merged["ok"] = out.ok
    merged["error"] = out.error
    merged["stall_stage"] = out.stall_stage
    merged["body"] = out.body or merged.get("body")
    for key, value in (out.extras or {}).items():
        merged[key] = value
    merged["extras"] = dict(out.extras or {})
    if merged.get("harvest_provenance") is None:
        merged["harvest_provenance"] = payload.get("harvest_provenance") or "chat"
    results = merged.get("results")
    if isinstance(results, list) and results:
        last = dict(results[-1])
        last["ok"] = True
        last["error"] = None
        if preserved_err:
            last["skill_receipt_warning"] = preserved_err
        for key in ("archive_verdict", "verdict_source"):
            if key in merged:
                last[key] = merged[key]
        results = list(results)
        results[-1] = last
        merged["results"] = results
    return merged


def _apply_archive_verdict_fallback_terminal_payload(
    payload: dict[str, Any],
    *,
    execution_id: str,
    purpose: str | None,
) -> dict[str, Any]:
    if payload.get("ok") or not _promotion_allowed(purpose=purpose):
        return payload
    err = str(payload.get("error") or "")
    if not _is_receipt_only_error(err):
        return payload
    archive_uri = _payload_archive_uri(payload)
    if not archive_uri:
        return payload
    extras = dict(payload.get("extras") or {})
    for key in (
        "archive_verdict",
        "verdict_source",
        "skill_receipt_warning",
        "warnings",
    ):
        if key in payload and key not in extras:
            extras[key] = payload[key]
    result = CdpGenerateResult(
        ok=False,
        body=str(payload.get("body") or ""),
        execution_id=execution_id,
        satellite_execution_id=execution_id,
        prompt_uri="",
        picker_model="",
        archive_uri=archive_uri,
        error=err,
        stall_stage=payload.get("stall_stage"),
        extras=extras,
    )
    out = _salvage_archive_verdict_on_result(result)
    if not out.ok:
        return payload
    return _merge_promoted_payload(payload, out, preserved_err=err or None)


def apply_archive_verdict_fallback_terminal_payload(
    payload: dict[str, Any],
    *,
    execution_id: str,
    purpose: str | None = None,
) -> dict[str, Any]:
    """Apply archive verdict salvage on a cdp_ask ``finish_execution`` payload."""
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
