"""ACT-RECEIPT grammar — emit/parse helpers for operator-proxy act verification.

Episode self-report without independent evidence resolve does not satisfy
verification (A1). Shipped ``commission_kind`` values only; unknown kinds reject.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

SHIPPED_COMMISSION_KINDS: frozenset[str] = frozenset(
    {"agent_bus_request", "charter_enroll"}
)
FENCE_TAG = "act-receipt"
_FENCE_RE = re.compile(
    rf"```{re.escape(FENCE_TAG)}\s*\n(.*?)\n```",
    re.DOTALL | re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class ActReceipt:
    """Parsed ACT-RECEIPT payload."""

    commission_kind: str
    evidence_uri: str
    trigger_id: str | None = None
    execution_id: str | None = None


def format_act_receipt(
    *,
    commission_kind: str,
    evidence_uri: str,
    trigger_id: str | None = None,
    execution_id: str | None = None,
) -> str:
    """Render markdown fence tagged ``act-receipt`` with one JSON object body."""
    kind = commission_kind.strip()
    if kind not in SHIPPED_COMMISSION_KINDS:
        raise ValueError(f"unsupported commission_kind: {kind}")
    payload: dict[str, Any] = {
        "act_receipt": True,
        "commission_kind": kind,
        "evidence_uri": evidence_uri,
    }
    if trigger_id:
        payload["trigger_id"] = trigger_id
    if execution_id:
        payload["execution_id"] = execution_id
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    return f"```{FENCE_TAG}\n{body}\n```"


def _parse_json_object(raw: str) -> ActReceipt | None:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or not data.get("act_receipt"):
        return None
    kind = str(data.get("commission_kind") or "").strip()
    if kind not in SHIPPED_COMMISSION_KINDS:
        return None
    evidence_uri = str(data.get("evidence_uri") or "").strip()
    if not evidence_uri:
        return None
    trigger_id = data.get("trigger_id")
    execution_id = data.get("execution_id")
    return ActReceipt(
        commission_kind=kind,
        evidence_uri=evidence_uri,
        trigger_id=str(trigger_id) if trigger_id else None,
        execution_id=str(execution_id) if execution_id else None,
    )


def parse_act_receipt(text: str) -> ActReceipt | None:
    """Parse fenced ``act-receipt`` block or raw JSON with ``act_receipt: true``."""
    if not text or not text.strip():
        return None
    match = _FENCE_RE.search(text)
    if match:
        return _parse_json_object(match.group(1).strip())
    stripped = text.strip()
    if stripped.startswith("{"):
        return _parse_json_object(stripped)
    return None


__all__ = [
    "ActReceipt",
    "FENCE_TAG",
    "SHIPPED_COMMISSION_KINDS",
    "format_act_receipt",
    "parse_act_receipt",
]
