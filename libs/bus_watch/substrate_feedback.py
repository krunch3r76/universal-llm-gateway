"""Substrate-rot finding extraction from implement closeout text."""

from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from typing import Any

from substrate_graph_write import write_claim
from substrate_graph_write.write import _DEFAULT_EVIDENCE

# Lines reporting a green test run must never classify as rot — the prior
# ``"warning"`` marker matched pytest summaries like ``77 passed, 3 warnings``.
_PASSING_RUN_RE = re.compile(
    r"(?:^|\s)(?:→\s*)?\d+\s+passed(?:,\s*\d+\s+warnings?)?\s+in\s+[\d.]+s",
    re.IGNORECASE,
)
_AC_PASS_RE = re.compile(r"\bAC\d+\b[^|\n]*\bPASS\b", re.IGNORECASE)
_EXIT_ZERO_WITH_PASS_RE = re.compile(
    r"exit\s*code\s*[=:]\s*0\b.*\bpassed\b|\bpassed\b.*exit\s*code\s*[=:]\s*0\b",
    re.IGNORECASE,
)

_PYTEST_FAIL_SUMMARY_RE = re.compile(
    r"\d+\s+failed\b.*\bin\s+[\d.]+s",
    re.IGNORECASE,
)

_FAILURE_SEMANTICS_RE = re.compile(
    r"(?:"
    r"\b(?:failed|failure|error)\b|"
    r"exit\s*code\s*[=:]\s*[1-9]\d*|"
    r"returncode\s*[=:]\s*[1-9]\d*|"
    r"\bstatus:\s*(?:failed|blocked|error)\b|"
    r'"status"\s*:\s*"(?:failed|blocked|error|partial)"|'
    r"\binfra\s+rot\b|"
    r"\blint\s+debt\b|"
    r"\baudit\s+warnings?\b|"
    r"\bpre-existing\b[^.\n]{0,80}\b(?:warning|fail|error)"
    r")",
    re.IGNORECASE,
)

_ENTITY_ID_LINE_RE = re.compile(r"(?im)^entity_id:\s*(\S+)")
_TODO_TOKEN_RE = re.compile(r"\btodo:[a-z0-9][a-z0-9._-]*", re.IGNORECASE)

_NEAR_DUP_BLOCK_THRESHOLD = 1.0
_FEEDBACK_CONFIDENCE = "suspected"
_FEEDBACK_DERIVATION = "inference"


def _line_carries_identical_true(line: str) -> bool:
    """True when a scraped probe line already carries ``\"identical\": true``."""
    try:
        payload = json.loads(line.strip())
    except json.JSONDecodeError:
        return False
    return isinstance(payload, dict) and payload.get("identical") is True


def _line_reports_passing_run(line: str) -> bool:
    """True when the line is a green run summary or explicit AC pass."""
    if _PASSING_RUN_RE.search(line):
        return True
    if _AC_PASS_RE.search(line):
        return True
    if _EXIT_ZERO_WITH_PASS_RE.search(line):
        return True
    return False


def _line_has_failure_semantics(line: str) -> bool:
    """True when the line carries rot/failure semantics, not benign wording."""
    if not line.strip():
        return False
    if _line_carries_identical_true(line):
        return False
    if _line_reports_passing_run(line):
        return False
    return _FAILURE_SEMANTICS_RE.search(line) is not None


def _findings_from_closeout_json(payload: dict[str, Any]) -> list[str] | None:
    """Return findings from a structured closeout envelope, or ``None`` to fall through."""
    status = str(payload.get("status") or "").lower()
    if status in {"failed", "blocked", "error"}:
        summary = str(payload.get("summary") or status)
        return [summary.strip()] if summary.strip() else [status]

    verification = payload.get("verification")
    failing: list[str] = []
    if isinstance(verification, list):
        for row in verification:
            if not isinstance(row, dict):
                continue
            exit_code = row.get("exit_code")
            register = str(row.get("exit_code_register") or "").lower()
            if isinstance(exit_code, int) and exit_code != 0:
                cmd = str(row.get("command") or "verification")
                failing.append(f"{cmd} exit_code={exit_code} ({register or 'observed'})")
            elif register == "observed" and exit_code == 0:
                continue
    if failing:
        return failing

    if status == "partial":
        summary = str(payload.get("summary") or "status:partial")
        return [summary.strip()] if summary.strip() else ["status:partial"]

    if status == "complete":
        return []

    return None


def extract_substrate_findings(text: str | None) -> list[str]:
    """Return plain-language lines that carry substrate rot / failure semantics.

    Prior substring markers (``warning``, ``substrate``, ``audit`` alone) are
    rejected: a green pytest summary or access-line tool name must not mint rot.
    """
    if not text:
        return []
    stripped = text.strip()
    if stripped.startswith("{"):
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            from_json = _findings_from_closeout_json(payload)
            if from_json is not None:
                return from_json

    findings: list[str] = []
    for line in text.splitlines():
        if not _line_has_failure_semantics(line):
            continue
        cleaned = line.strip()
        if cleaned and cleaned not in findings:
            findings.append(cleaned)
    summaries = [f for f in findings if _PYTEST_FAIL_SUMMARY_RE.search(f)]
    if summaries:
        return [summaries[-1]]
    return findings


def resolve_substrate_feedback_entity_id(*, subject: str, body: str) -> str | None:
    """Resolve a graph-write target from directive subject/body."""
    blob = "\n".join(part for part in (subject, body) if part)
    entity_match = _ENTITY_ID_LINE_RE.search(blob)
    if entity_match:
        return entity_match.group(1).strip()
    todo_match = _TODO_TOKEN_RE.search(blob)
    if todo_match:
        return todo_match.group(0)
    return None


def _near_duplicate_score(claim: str, existing: str) -> float:
    return SequenceMatcher(None, claim.lower(), existing.lower()).ratio()


def _fetch_active_claims(entity_id: str) -> list[str]:
    """Best-effort read of active claims on *entity_id* for duplicate blocking."""
    from substrate_graph_write.write import make_sync_client
    from transport_utils import DEFAULT_CORTEX_URL

    body = {
        "tool": "assertions",
        "arguments": json.dumps(
            {"entity_id": entity_id, "superseded": False, "limit": 50}
        ),
        "surface": "code",
        "via_adapter": True,
        "seat": "cursor-sdk",
    }
    try:
        with make_sync_client(DEFAULT_CORTEX_URL, timeout=15.0) as client:
            response = client.post("/dispatch", json=body)
    except Exception:
        return []
    if response.status_code >= 400:
        return []
    try:
        parsed = response.json()
    except ValueError:
        return []
    items = parsed.get("items") if isinstance(parsed, dict) else None
    if not isinstance(items, list):
        return []
    claims: list[str] = []
    for item in items:
        if isinstance(item, dict):
            claim = item.get("claim")
            if isinstance(claim, str) and claim.strip():
                claims.append(claim.strip())
    return claims


def _blocked_near_duplicate(entity_id: str, claim: str) -> dict[str, Any] | None:
    for existing in _fetch_active_claims(entity_id):
        score = _near_duplicate_score(claim, existing)
        if score >= _NEAR_DUP_BLOCK_THRESHOLD:
            return {
                "blocked": True,
                "reason": "near_duplicate",
                "score": round(score, 4),
                "matched_claim": existing,
            }
    return None


def write_substrate_feedback_claim(
    *,
    entity_id: str,
    findings: list[str],
    evidence_uris: list[str] | None = None,
) -> dict[str, Any]:
    """Assert a substrate-feedback claim with evidence-appropriate confidence."""
    verbatim = "; ".join(findings[:5])
    claim = f"Substrate rot signal (verbatim): {verbatim}"
    blocked = _blocked_near_duplicate(entity_id, claim)
    if blocked:
        return blocked
    return write_claim(
        entity_id=entity_id,
        claim=claim,
        confidence=_FEEDBACK_CONFIDENCE,
        derivation_type=_FEEDBACK_DERIVATION,
        evidence=_DEFAULT_EVIDENCE,
        evidence_uris=evidence_uris,
    )


__all__ = [
    "extract_substrate_findings",
    "resolve_substrate_feedback_entity_id",
    "write_substrate_feedback_claim",
]
