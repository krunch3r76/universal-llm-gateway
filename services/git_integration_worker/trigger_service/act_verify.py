"""Independent act verification after trigger reconcile — outside reconcile txn."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from claude_bundles.act_receipt import ActReceipt, parse_act_receipt
from claude_bundles.operator_proxy_mission import OPERATOR_PROXY_MISSION_PURPOSES
from durable_io.atomic import durable_write_text
from implement_admission.closeout_helpers import cortex_files_root

from services.git_integration_worker.trigger_service.models import (
    PROMPT_PREFIX,
    TriggerRow,
)

ACT_RECEIPT_REL = "act-receipt.md"
ACT_STATUS_NA = "n/a"
ACT_STATUS_PENDING = "pending"
ACT_STATUS_CLAIMED = "claimed"
ACT_STATUS_VERIFIED = "verified"
ACT_STATUS_UNVERIFIED = "unverified"


def effective_require_act(row: TriggerRow) -> bool:
    """Tri-state require_act_receipt with verify-time default (A2)."""
    if row.require_act_receipt is not None:
        return bool(row.require_act_receipt)
    return row.purpose in OPERATOR_PROXY_MISSION_PURPOSES


def dedicated_receipt_uri(trigger_id: str) -> str:
    return f"cortex://{PROMPT_PREFIX}/{trigger_id}/{ACT_RECEIPT_REL}"


def _read_cortex_uri(uri: str) -> str | None:
    if not uri.startswith("cortex://"):
        return None
    rel = uri.removeprefix("cortex://")
    path = cortex_files_root() / rel
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


class EvidenceResolver(Protocol):
    def resolve(
        self,
        receipt: ActReceipt,
        *,
        row: TriggerRow,
        fired_at: str | None,
    ) -> tuple[bool, str | None]:
        """Return (verified, reason_code)."""


@dataclass(frozen=True, slots=True)
class NullEvidenceResolver:
    """Fail-closed resolver — evidence never verifies (tests / resolver down)."""

    reason: str = "evidence_resolver_unavailable"

    def resolve(
        self,
        receipt: ActReceipt,
        *,
        row: TriggerRow,
        fired_at: str | None,
    ) -> tuple[bool, str | None]:
        del receipt, row, fired_at
        return False, self.reason


@dataclass(frozen=True, slots=True)
class CallableEvidenceResolver:
    """Inject a callable for unit tests and live probes."""

    fn: Callable[[ActReceipt, TriggerRow, str | None], tuple[bool, str | None]]

    def resolve(
        self,
        receipt: ActReceipt,
        *,
        row: TriggerRow,
        fired_at: str | None,
    ) -> tuple[bool, str | None]:
        return self.fn(receipt, row, fired_at)


def fetch_receipt_text(
    row: TriggerRow,
    *,
    archive_body: str | None = None,
) -> tuple[str | None, str | None]:
    """Fetch receipt outside reconcile txn. Returns (text, reader_error)."""
    dedicated = dedicated_receipt_uri(row.id)
    text = _read_cortex_uri(dedicated)
    if text:
        return text, None
    if archive_body:
        return archive_body, None
    if row.archive_uri:
        archive_text = _read_cortex_uri(row.archive_uri)
        if archive_text:
            return archive_text, None
        return None, "archive_read_failed"
    return None, "receipt_not_found"


def verify_act_for_row(
    row: TriggerRow,
    *,
    archive_body: str | None = None,
    resolver: EvidenceResolver | None = None,
) -> dict[str, Any]:
    """Compute act fields after terminal reconcile — no store mutation."""
    if not effective_require_act(row):
        return {
            "act_status": ACT_STATUS_NA,
            "act_evidence_uri": None,
            "act_error": None,
            "event": None,
            "event_payload": {},
        }
    text, reader_err = fetch_receipt_text(row, archive_body=archive_body)
    if reader_err == "archive_read_failed":
        return {
            "act_status": ACT_STATUS_PENDING,
            "act_evidence_uri": None,
            "act_error": None,
            "event": None,
            "event_payload": {"reason_code": reader_err},
        }
    if not text:
        return {
            "act_status": ACT_STATUS_UNVERIFIED,
            "act_evidence_uri": None,
            "act_error": "malformed_or_missing_receipt",
            "event": "giw.trigger.act_unverified",
            "event_payload": {
                "trigger_id": row.id,
                "commission_kind": None,
                "evidence_uri": None,
                "reason_code": "malformed_or_missing_receipt",
            },
        }
    receipt = parse_act_receipt(text)
    if receipt is None:
        return {
            "act_status": ACT_STATUS_UNVERIFIED,
            "act_evidence_uri": None,
            "act_error": "malformed_or_missing_receipt",
            "event": "giw.trigger.act_unverified",
            "event_payload": {
                "trigger_id": row.id,
                "commission_kind": None,
                "evidence_uri": None,
                "reason_code": "malformed_or_missing_receipt",
            },
        }
    evidence_resolver = resolver or NullEvidenceResolver()
    verified, reason = evidence_resolver.resolve(
        receipt,
        row=row,
        fired_at=row.fired_at,
    )
    if verified:
        return {
            "act_status": ACT_STATUS_VERIFIED,
            "act_evidence_uri": receipt.evidence_uri,
            "act_error": None,
            "event": "giw.trigger.act_verified",
            "event_payload": {
                "trigger_id": row.id,
                "commission_kind": receipt.commission_kind,
                "evidence_uri": receipt.evidence_uri,
                "reason_code": "evidence_resolved",
            },
        }
    return {
        "act_status": ACT_STATUS_CLAIMED
        if reason == "evidence_resolver_unavailable"
        else ACT_STATUS_UNVERIFIED,
        "act_evidence_uri": receipt.evidence_uri,
        "act_error": reason or "evidence_unresolved",
        "event": "giw.trigger.act_unverified",
        "event_payload": {
            "trigger_id": row.id,
            "commission_kind": receipt.commission_kind,
            "evidence_uri": receipt.evidence_uri,
            "reason_code": reason or "evidence_unresolved",
        },
    }


def write_dedicated_receipt(trigger_id: str, text: str) -> Path:
    """Persist act receipt at deterministic cortex location (episode / probe helper)."""
    rel = f"{PROMPT_PREFIX}/{trigger_id}/{ACT_RECEIPT_REL}"
    dest = cortex_files_root() / rel
    durable_write_text(dest, text, retain_store_root=cortex_files_root())
    return dest


__all__ = [
    "ACT_STATUS_CLAIMED",
    "ACT_STATUS_NA",
    "ACT_STATUS_PENDING",
    "ACT_STATUS_UNVERIFIED",
    "ACT_STATUS_VERIFIED",
    "CallableEvidenceResolver",
    "EvidenceResolver",
    "NullEvidenceResolver",
    "dedicated_receipt_uri",
    "effective_require_act",
    "fetch_receipt_text",
    "verify_act_for_row",
    "write_dedicated_receipt",
]
