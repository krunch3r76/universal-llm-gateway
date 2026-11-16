"""Archive-body verdict when skill_receipt_unverified would fail-closed (a:27142)."""

from __future__ import annotations

import logging
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

from claude_bundles.cdp_model_endpoint import CdpGenerateResult

logger = logging.getLogger(__name__)

_SKILL_RECEIPT = "skill_receipt_unverified"
_CANONICAL_VERDICT_LINE_RE = re.compile(
    r"(?m)^VERDICT:[ \t]*(?P<raw>.+?)[ \t]*$",
    re.IGNORECASE,
)
_LOOSE_VERDICT_LINE_RE = re.compile(
    r"(?i)\bverdict\b[^\w\n]{0,4}?(?:[:=\u2014\u2013]|\s-\s)[^\w\n]*(?P<token>\w+)",
)
_REQUEST_CHANGES_BODY_RE = re.compile(r"(?i)\bREQUEST_CHANGES\b")
_BARE_VERDICT_COLON_LINE_RE = re.compile(r"(?im)^VERDICT:\s*$")
_PROMPT_ECHO_VERDICT_RE = re.compile(r"(?im)^VERDICT\s*<")
_ADVANCE_VERDICTS = frozenset({"APPROVE", "APPROVE_WITH_NITS"})
_VERDICT_ENUM_TOKENS = frozenset(
    {
        "APPROVE",
        "APPROVE_WITH_NITS",
        "REQUEST_CHANGES",
        "RATIFY",
        "RATIFY_WITH_CONDITIONS",
        "PASS",
        "SHIP",
    }
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
    """Return the first ``^VERDICT:`` line token, or None."""
    match = _CANONICAL_VERDICT_LINE_RE.search(body)
    if not match:
        return None
    return _token_from_verdict_line_raw(match.group("raw"))


def _all_verdict_line_tokens(body: str) -> list[str] | None:
    """Every ``^VERDICT:`` token in document order; None if any line is unparseable."""
    tokens: list[str] = []
    for match in _CANONICAL_VERDICT_LINE_RE.finditer(body):
        token = _token_from_verdict_line_raw(match.group("raw"))
        if token is None:
            return None
        tokens.append(token)
    return tokens


def _unified_advance_verdict(body: str) -> str | None:
    """Promote only when every ``^VERDICT:`` line names the same advance token."""
    tokens = _all_verdict_line_tokens(body)
    if not tokens:
        return None
    if len(set(tokens)) != 1:
        return None
    token = tokens[0]
    if not _verdict_advances(token):
        return None
    return token


def parse_archive_verdict(body: str) -> str | None:
    """Return canonical ``VERDICT:`` token from archive prose (promotion parser only)."""
    return parse_canonical_verdict_line(body)


def _verdict_advances(token: str | None) -> bool:
    return token in _ADVANCE_VERDICTS if token else False


def _normalize_verdict_token(raw: str) -> str:
    return raw.upper().replace("-", "_")


def _line_bounds(body: str, index: int) -> tuple[int, int]:
    start = body.rfind("\n", 0, index) + 1
    end = body.find("\n", index)
    if end == -1:
        end = len(body)
    return start, end


def _clean_pipe_segment(segment: str) -> str:
    """Strip whitespace and light markdown wrappers from a pipe segment."""
    cleaned = segment.strip()
    if len(cleaned) >= 2 and cleaned[0] == "`" and cleaned[-1] == "`":
        cleaned = cleaned[1:-1].strip()
    if len(cleaned) >= 4 and cleaned.startswith("**") and cleaned.endswith("**"):
        cleaned = cleaned[2:-2].strip()
    return cleaned.strip()


def _segment_names_enum_token(segment: str) -> str | None:
    """Return the verdict enum token only when the segment is exactly that token."""
    candidates = [_clean_pipe_segment(segment)]
    if ":" in segment:
        candidates.append(_clean_pipe_segment(segment.rsplit(":", 1)[-1]))
    for cleaned in candidates:
        normalized = _normalize_verdict_token(cleaned)
        if normalized in _VERDICT_ENUM_TOKENS:
            return normalized
    return None


def _line_is_pure_verdict_enum_echo(line: str) -> bool:
    """True when ``REQUEST_CHANGES`` appears only in a pipe-delimited enum listing."""
    stripped = line.strip()
    if not _REQUEST_CHANGES_BODY_RE.search(stripped):
        return False
    loose = _LOOSE_VERDICT_LINE_RE.search(stripped)
    if loose and _normalize_verdict_token(loose.group("token")) == "REQUEST_CHANGES":
        return False
    if "|" not in stripped:
        return False
    segments = [seg.strip() for seg in stripped.split("|") if seg.strip()]
    if len(segments) < 2:
        return False
    saw_request_changes = False
    for seg in segments:
        token = _segment_names_enum_token(seg)
        if token is None:
            return False
        if token == "REQUEST_CHANGES":
            saw_request_changes = True
    return saw_request_changes


def _body_request_changes_blocks_promotion(body: str) -> bool:
    """Fail-closed when ``REQUEST_CHANGES`` appears outside pure enum-echo lines."""
    for match in _REQUEST_CHANGES_BODY_RE.finditer(body):
        start, end = _line_bounds(body, match.start())
        if not _line_is_pure_verdict_enum_echo(body[start:end]):
            return True
    return False


def _loose_veto_blocks_promotion(body: str) -> bool:
    """Fail-closed when any loose ``verdict`` line names a non-advance token."""
    for match in _LOOSE_VERDICT_LINE_RE.finditer(body):
        token = _normalize_verdict_token(match.group("token"))
        if token == "VERDICT":
            return True
        if not _verdict_advances(token):
            return True
    return False


def _bare_verdict_colon_blocks_promotion(body: str) -> bool:
    """Fail-closed when a canonical ``VERDICT:`` line has no token."""
    return _BARE_VERDICT_COLON_LINE_RE.search(body) is not None


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


def apply_archive_verdict_fallback_gated(
    result: CdpGenerateResult,
    *,
    purpose: str | None,
) -> CdpGenerateResult:
    """Archive verdict salvage for stargate generate (purpose-gated, fail-closed)."""
    if not _promotion_allowed(purpose=purpose):
        return result
    out = apply_archive_verdict_fallback(result)
    if not out.ok:
        return out
    extras = dict(out.extras or {})
    if extras.get("harvest_provenance") is None:
        extras["harvest_provenance"] = "chat"
    return replace(out, extras=extras)


def apply_archive_verdict_fallback(result: CdpGenerateResult) -> CdpGenerateResult:
    """When skill_receipt_unverified, prefer a canonical archive ``VERDICT:`` line."""
    if not _is_receipt_only_error(result.error):
        return result
    if result.ok:
        return result
    text = _read_archive_text(result.archive_uri)
    if not text:
        return result
    if _PROMPT_ECHO_VERDICT_RE.search(text):
        return result
    if _body_request_changes_blocks_promotion(text):
        return result
    if _bare_verdict_colon_blocks_promotion(text):
        return result
    if _loose_veto_blocks_promotion(text):
        return result
    verdict = _unified_advance_verdict(text)
    if not verdict:
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
    return replace(
        result,
        ok=True,
        body=result.body or text,
        stall_stage=None,
        error=None,
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
    out = apply_archive_verdict_fallback(result)
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
    "apply_archive_verdict_fallback_gated",
    "apply_archive_verdict_fallback_terminal_payload",
    "parse_archive_verdict",
    "parse_canonical_verdict_line",
]
