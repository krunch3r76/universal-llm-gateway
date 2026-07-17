"""Structured recon waiver parsing and validation for implement admission."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

RECON_WAIVE_REASON_CODES = frozenset(
    {
        "ratified_on_prior_spec_revision",
        "design_pre_adjudicated",
        "operator_directive",
        "path_sim_self_certify",
    }
)

_UNKNOWN_REASON_CODE = "recon_waive_reason_code_unknown"


@dataclass(frozen=True, slots=True)
class WaiverInfo:
    waived: bool
    waived_by: str | None = None
    reason_code: str | None = None
    reason: str | None = None
    spec_sha256: str | None = None
    waived_at: str | None = None

    def to_attr_json(self) -> str:
        payload = {
            "waived_by": self.waived_by,
            "reason_code": self.reason_code,
            "reason": self.reason,
            "spec_sha256": self.spec_sha256,
            "waived_at": self.waived_at,
        }
        return json.dumps(payload, separators=(",", ":"), sort_keys=True)

    def to_gate_sibling(self) -> dict[str, Any] | None:
        if not self.waived:
            return None
        return {
            "reason_code": self.reason_code,
            "reason": self.reason,
            "waived_by": self.waived_by,
            "waived_at": self.waived_at,
            "spec_sha256": self.spec_sha256,
        }

    def equivalent_to(self, other: WaiverInfo | None) -> bool:
        if other is None:
            return not self.waived
        if self.waived != other.waived:
            return False
        if not self.waived:
            return True
        return (
            self.reason_code == other.reason_code
            and self.reason == other.reason
            and self.waived_by == other.waived_by
            and self.spec_sha256 == other.spec_sha256
        )

    def event_payload(self) -> dict[str, Any]:
        return {
            "waived_by": self.waived_by,
            "reason_code": self.reason_code,
            "reason": self.reason,
            "spec_sha256": self.spec_sha256,
            "waived_at": self.waived_at,
        }


def _optional_str(raw: Any) -> str | None:
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def parse_recon_waiver(raw: Any) -> WaiverInfo | None:
    """Parse attrs.recon_waived; never raises."""
    if raw is None:
        return None
    if not isinstance(raw, str):
        return None
    stripped = raw.strip()
    if not stripped:
        return None
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return WaiverInfo(waived=True)
    if not isinstance(parsed, dict):
        return WaiverInfo(waived=True)
    return WaiverInfo(
        waived=True,
        waived_by=_optional_str(parsed.get("waived_by")),
        reason_code=_optional_str(parsed.get("reason_code")),
        reason=_optional_str(parsed.get("reason")),
        spec_sha256=_optional_str(parsed.get("spec_sha256")),
        waived_at=_optional_str(parsed.get("waived_at")),
    )


def recon_waived_bool(raw: Any) -> bool:
    """Truthiness contract: any non-empty string ⇒ waived."""
    info = parse_recon_waiver(raw)
    return info is not None and info.waived


_SPEC_SHA256_PREFIX = "spec_sha256:"


def _bare_spec_hex(hash_value: str | None) -> str | None:
    if hash_value is None:
        return None
    text = str(hash_value).strip()
    if not text:
        return None
    if text.lower().startswith(_SPEC_SHA256_PREFIX):
        text = text[len(_SPEC_SHA256_PREFIX) :]
    return text or None


def waiver_matches_current_spec(
    waiver: WaiverInfo,
    current_spec_hash_uri: str | None,
) -> bool:
    """True only when the waiver's spec_sha256 matches the current dense spec."""
    if not waiver.waived:
        return False
    current_hex = _bare_spec_hex(current_spec_hash_uri)
    if current_hex is None:
        return False
    waiver_hex = _bare_spec_hex(waiver.spec_sha256)
    if waiver_hex is None:
        return False
    return waiver_hex == current_hex


def resolve_effective_recon_waived(
    raw: Any,
    current_spec_hash_uri: str | None,
) -> tuple[bool, WaiverInfo | None, bool]:
    """Return (effective_waived, parsed_waiver, stale_discarded)."""
    waiver = parse_recon_waiver(raw)
    if not recon_waived_bool(raw) or waiver is None:
        return False, waiver, False
    if waiver_matches_current_spec(waiver, current_spec_hash_uri):
        return True, waiver, False
    return False, waiver, True


def validate_recon_waive_reason_code(code: str | None) -> dict[str, Any] | None:
    """Return {error, code} when invalid; None when absent or valid."""
    if code is None:
        return None
    stripped = code.strip()
    if not stripped:
        return None
    if stripped not in RECON_WAIVE_REASON_CODES:
        return {
            "error": f"unknown recon_waive_reason_code: {stripped!r}",
            "code": _UNKNOWN_REASON_CODE,
        }
    return None


def build_structured_waiver(
    *,
    reason_code: str,
    reason: str | None,
    waived_by: str,
    spec_sha256: str,
    waived_at: str | None = None,
) -> WaiverInfo:
    return WaiverInfo(
        waived=True,
        waived_by=waived_by,
        reason_code=reason_code.strip(),
        reason=_optional_str(reason),
        spec_sha256=spec_sha256,
        waived_at=waived_at or datetime.now(UTC).isoformat(),
    )


__all__ = [
    "RECON_WAIVE_REASON_CODES",
    "WaiverInfo",
    "build_structured_waiver",
    "parse_recon_waiver",
    "recon_waived_bool",
    "resolve_effective_recon_waived",
    "validate_recon_waive_reason_code",
    "waiver_matches_current_spec",
]
