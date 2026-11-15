"""Archive-body verdict when skill_receipt_unverified would fail-closed (a:27142)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

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
    """When skill_receipt_unverified, prefer a parseable archive verdict."""
    err = (result.error or "").lower()
    if _SKILL_RECEIPT not in err:
        return result
    if result.ok:
        return result
    text = _read_archive_text(result.archive_uri)
    if not text:
        return result
    verdict = parse_archive_verdict(text)
    if not verdict:
        return result
    preserved_err = result.error
    extras = dict(result.extras or {})
    extras["verdict_source"] = "archive"
    extras["archive_verdict"] = verdict
    head = verdict.split()[0].upper()
    root = head.split("_")[0].split("-")[0]
    advance = root in {"APPROVE", "PASS", "SHIP", "RATIFY"}
    if advance and preserved_err:
        extras["skill_receipt_warning"] = preserved_err
        warnings = list(extras.get("warnings") or [])
        if preserved_err not in warnings:
            warnings.append(preserved_err)
        extras["warnings"] = warnings
    return CdpGenerateResult(
        ok=advance,
        body=result.body or text,
        execution_id=result.execution_id,
        satellite_execution_id=result.satellite_execution_id,
        prompt_uri=result.prompt_uri,
        picker_model=result.picker_model,
        archive_uri=result.archive_uri,
        content_proof_uri=result.content_proof_uri,
        content_proof_sha256=result.content_proof_sha256,
        stall_stage=None if advance else result.stall_stage,
        error=None if advance else result.error,
        substrate=result.substrate,
        cost_source=result.cost_source,
        poll_snapshots=result.poll_snapshots,
        extras=extras,
    )


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


def apply_archive_verdict_fallback_terminal_payload(
    payload: dict[str, Any],
    *,
    execution_id: str,
) -> dict[str, Any]:
    """Apply archive verdict salvage on a cdp_ask ``finish_execution`` payload."""
    err = str(payload.get("error") or "")
    if _SKILL_RECEIPT not in err.lower() or payload.get("ok"):
        return payload
    archive_uri = _payload_archive_uri(payload)
    if not archive_uri:
        return payload
    extras = dict(payload.get("extras") or {})
    for key in ("archive_verdict", "verdict_source", "skill_receipt_warning", "warnings"):
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
    out = apply_archive_verdict_fallback(result)
    if out.ok == result.ok:
        return payload
    merged = dict(payload)
    merged["ok"] = out.ok
    merged["error"] = out.error
    merged["stall_stage"] = out.stall_stage
    merged["body"] = out.body or merged.get("body")
    for key, value in (out.extras or {}).items():
        merged[key] = value
    merged["extras"] = dict(out.extras or {})
    return merged


__all__ = [
    "apply_archive_verdict_fallback",
    "apply_archive_verdict_fallback_terminal_payload",
    "parse_archive_verdict",
]
