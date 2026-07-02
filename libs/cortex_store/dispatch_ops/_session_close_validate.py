"""session_close doc_validate — wraps preflight, maps audit/warnings to gates."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ._session_close_doc_type import (
    _SESSION_CLOSE_OPTIONAL_FIELDS,
    _SESSION_CLOSE_REQUIRED_FIELDS,
    session_close_attestation_tokens,
)

_SESSION_CLOSE_PAYLOAD_KEYS = frozenset(
    {*_SESSION_CLOSE_REQUIRED_FIELDS, *_SESSION_CLOSE_OPTIONAL_FIELDS}
)


@dataclass(frozen=True, slots=True)
class SessionCloseValidateVerdict:
    passed: bool
    gates: list[dict[str, Any]]
    preflight: dict[str, Any]
    reason: str | None = None


def extract_session_close_payload(kwargs: dict[str, Any]) -> dict[str, Any]:
    return {
        key: kwargs[key]
        for key in _SESSION_CLOSE_PAYLOAD_KEYS
        if key in kwargs and kwargs[key] is not None
    }


def merge_session_close_payload(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Flat kwargs first; optional ``text`` JSON object fills missing close fields."""
    payload = extract_session_close_payload(kwargs)
    text = kwargs.get("text")
    if not isinstance(text, str) or not text.strip():
        return payload
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return payload
    if not isinstance(parsed, dict):
        return payload
    for key in _SESSION_CLOSE_PAYLOAD_KEYS:
        if key not in payload and key in parsed and parsed[key] is not None:
            payload[key] = parsed[key]
    return payload


def _gate(
    *,
    gate_id: str,
    status: str,
    detail: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    row: dict[str, Any] = {"gate": gate_id, "status": status}
    if detail:
        row["detail"] = detail
    row.update(extra)
    return row


def _audit_gates(audit: dict[str, Any]) -> list[dict[str, Any]]:
    gates: list[dict[str, Any]] = []
    if audit.get("blocked"):
        criticals = audit.get("criticals") or []
        detail = audit.get("error") or "session audit blocked close"
        gates.append(
            _gate(
                gate_id="audit_blocked",
                status="failed",
                detail=detail,
                criticals_count=len(criticals),
            )
        )
        return gates

    warning = audit.get("warning") or {}
    findings = warning.get("findings_sample") or warning.get("audit_findings") or []
    if not findings:
        gates.append(_gate(gate_id="audit", status="passed"))
        return gates

    by_kind = warning.get("by_kind") or {}
    for kind, count in sorted(by_kind.items()):
        gates.append(
            _gate(
                gate_id=f"audit.{kind}",
                status="failed",
                detail=f"{count} finding(s)",
                count=count,
            )
        )
    if not by_kind:
        gates.append(
            _gate(
                gate_id="audit",
                status="failed",
                detail=f"{len(findings)} audit finding(s)",
                gap_count=warning.get("gap_count", len(findings)),
            )
        )
    return gates


def _warning_gates(warnings: list[Any]) -> list[dict[str, Any]]:
    if not warnings:
        return [_gate(gate_id="warnings", status="passed")]
    return [
        _gate(
            gate_id="warnings",
            status="failed",
            detail=str(item),
        )
        for item in warnings
    ]


def validate_session_close_payload(payload: dict[str, Any]) -> SessionCloseValidateVerdict:
    from .ops_session_close import _op_session_close_preflight

    preflight = _op_session_close_preflight(**payload)
    gates: list[dict[str, Any]] = []

    if not preflight.get("ok"):
        reason = preflight.get("reason") or preflight.get("error") or "preflight_failed"
        gates.append(
            _gate(
                gate_id="preflight",
                status="failed",
                detail=str(preflight.get("error") or reason),
                reason=reason,
            )
        )
        return SessionCloseValidateVerdict(
            passed=False,
            gates=gates,
            preflight=preflight,
            reason=str(reason),
        )

    audit = preflight.get("audit") or {}
    gates.extend(_audit_gates(audit if isinstance(audit, dict) else {}))
    warnings = preflight.get("warnings") or []
    if isinstance(warnings, list):
        gates.extend(_warning_gates(warnings))

    failed = [g for g in gates if g.get("status") == "failed"]
    session_id = payload.get("session_id") or preflight.get("session_id")
    passed = not failed and bool(session_id)
    return SessionCloseValidateVerdict(
        passed=passed,
        gates=gates,
        preflight=preflight,
        reason=None if passed else (failed[0].get("reason") or failed[0].get("detail")),
    )


def session_close_validate_attestation_tokens(*, payload: dict[str, Any]) -> list[str]:
    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id.strip():
        return []
    return session_close_attestation_tokens(session_id=session_id.strip())


__all__ = [
    "SessionCloseValidateVerdict",
    "extract_session_close_payload",
    "merge_session_close_payload",
    "session_close_validate_attestation_tokens",
    "validate_session_close_payload",
]
