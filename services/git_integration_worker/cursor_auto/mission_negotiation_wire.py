"""Strict wire parser for directive-loop mission negotiation (Rival B)."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from agent_seat.registry import normalize_bus_address

NegotiationPhase = Literal["proposal", "counter", "agree", "ratify"]
NegotiationState = Literal[
    "OPEN",
    "AWAITING_RATIFICATION",
    "RATIFIED",
    "EXPIRED",
    "ROUND_LIMIT",
    "REFUSED",
]

NEGOTIATION_PHASES = frozenset({"proposal", "counter", "agree", "ratify"})
NEGOTIATION_DISPOSITIONS = frozenset(
    {
        "negotiation.countered",
        "negotiation.accepted",
        "negotiation.agreed",
        "negotiation.ratified",
        "negotiation.refused",
        "negotiation.duplicate",
        "negotiation.expired",
        "negotiation.round_limit",
    }
)
NEGOTIATION_STATES = frozenset(
    {"OPEN", "AWAITING_RATIFICATION", "RATIFIED", "EXPIRED", "ROUND_LIMIT", "REFUSED"}
)

_REQUIRED_FIELDS = frozenset(
    {
        "negotiation_phase",
        "negotiation_id",
        "revision",
        "in_reply_to_turn",
        "proposal_hash",
        "parent_thread",
        "objective",
        "scope",
        "out_of_scope",
        "acceptance",
        "vision",
        "idle_deadline",
    }
)
_PAYLOAD_FIELDS = frozenset(
    {
        "parent_thread",
        "objective",
        "scope",
        "out_of_scope",
        "acceptance",
        "vision",
    }
)
_KNOWN_NEGOTIATION_FIELDS = _REQUIRED_FIELDS | frozenset({"contract", "type"})
_EXECUTION_FIELDS = frozenset(
    {
        "desired_model",
        "desired_effort",
        "escalation",
        "model_knobs",
        "nest_under",
        "dispatch_id",
    }
)

_FIELD_RE = {
    name: re.compile(rf"^{re.escape(name)}:\s*(.+)$", re.MULTILINE | re.IGNORECASE)
    for name in (
        "negotiation_phase",
        "negotiation_id",
        "revision",
        "in_reply_to_turn",
        "proposal_hash",
        "parent_thread",
        "objective",
        "scope",
        "out_of_scope",
        "acceptance",
        "vision",
        "idle_deadline",
        "contract",
        "desired_model",
        "desired_effort",
        "escalation",
    )
}
_TYPE_RE = re.compile(r"^TYPE:\s*(\S+)", re.MULTILINE | re.IGNORECASE)
_HASH_RE = re.compile(r"^sha256:([0-9a-f]{64})$")
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class CanonicalMissionPayload:
    """Normalized mission tuple hashed for ``proposal_hash``."""

    parent_thread: str
    objective: str
    scope: str
    out_of_scope: str
    acceptance: str
    vision: str

    def as_dict(self) -> dict[str, str]:
        return {
            "parent_thread": self.parent_thread,
            "objective": self.objective,
            "scope": self.scope,
            "out_of_scope": self.out_of_scope,
            "acceptance": self.acceptance,
            "vision": self.vision,
        }


@dataclass(frozen=True, slots=True)
class ParsedNegotiationRequest:
    """Validated negotiation turn extracted from a DIRECTIVE body."""

    phase: NegotiationPhase
    negotiation_id: str
    revision: int
    in_reply_to_turn: int
    proposal_hash: str
    payload: CanonicalMissionPayload
    idle_deadline: str
    contract: str
    turn_type: str


@dataclass(frozen=True, slots=True)
class NegotiationParseError:
    """Structured refusal before ledger touch."""

    reason: str
    summary: str


def has_negotiation_phase(body: str) -> bool:
    """True when the body declares ``negotiation_phase:``."""
    return _FIELD_RE["negotiation_phase"].search(body or "") is not None


def is_mission_negotiation_request(body: str) -> bool:
    """True for a DIRECTIVE body carrying a negotiation phase marker."""
    text = body or ""
    if not has_negotiation_phase(text):
        return False
    match = _TYPE_RE.search(text)
    if match is None:
        return False
    return match.group(1).strip().upper() == "DIRECTIVE"


def negotiation_hop_conflict(body: str, *, continuity_hop: bool) -> bool:
    """True when negotiation fields combine with continuity hop markers."""
    if not has_negotiation_phase(body):
        return False
    if continuity_hop:
        return True
    is_hop, _ = _continuity_hop_from_body(body)
    return is_hop


def _continuity_hop_from_body(body: str) -> tuple[bool, str]:
    from services.git_integration_worker.cursor_auto.directive import (
        is_continuity_hop_request,
    )

    return is_continuity_hop_request(body)


def _field_value(body: str, name: str) -> str | None:
    match = _FIELD_RE[name].search(body or "")
    if match is None:
        return None
    return match.group(1).strip()


def _unknown_negotiation_fields(body: str) -> tuple[str, ...]:
    unknown: list[str] = []
    for line in (body or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in stripped:
            continue
        key = stripped.split(":", 1)[0].strip().lower()
        if key in {"type"}:
            continue
        if key in _KNOWN_NEGOTIATION_FIELDS or key in _EXECUTION_FIELDS:
            continue
        if key in {
            "density",
            "source_ref",
            "require_attended",
            "executor_bind",
            "evidence_required",
            "read-corpus",
            "read",
            "corpus",
        }:
            continue
        if key.startswith("negotiation_") or key in _KNOWN_NEGOTIATION_FIELDS:
            continue
        if re.match(r"^[a-z_][a-z0-9_-]*$", key):
            unknown.append(key)
    return tuple(sorted(set(unknown)))


def canonical_payload_from_body(body: str) -> CanonicalMissionPayload | None:
    """Build the canonical mission tuple from body fields."""
    values: dict[str, str] = {}
    for name in _PAYLOAD_FIELDS:
        raw = _field_value(body, name)
        if raw is None:
            return None
        values[name] = raw.strip()
    return CanonicalMissionPayload(**values)


def compute_proposal_hash(payload: CanonicalMissionPayload) -> str:
    """Return ``sha256:<hex>`` over the canonical JSON payload."""
    encoded = json.dumps(payload.as_dict(), sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def is_operator_proxy_sender(from_agent: str) -> bool:
    """True for CDP operator-proxy bus identities."""
    normalized = normalize_bus_address(from_agent)
    if normalized == "web-anthropic":
        return True
    return normalized.startswith("cdp-operator")


def is_cursor_auto_sender(from_agent: str) -> bool:
    """True when *from_agent* normalizes to the cursor-auto bus identity."""
    return normalize_bus_address(from_agent) == "cursor-auto"


def parse_negotiation_request(
    body: str,
    *,
    turn_type: str,
    from_agent: str,
) -> ParsedNegotiationRequest | NegotiationParseError:
    """Parse and validate one negotiation DIRECTIVE body."""
    text = body or ""
    if not has_negotiation_phase(text):
        return NegotiationParseError(
            reason="negotiation.malformed",
            summary="negotiation_phase marker absent",
        )
    if turn_type.upper() != "DIRECTIVE":
        return NegotiationParseError(
            reason="negotiation.type_refused",
            summary="negotiation marker requires TYPE: DIRECTIVE",
        )
    if turn_type.upper() == "DISPOSITION":
        return NegotiationParseError(
            reason="negotiation.direction_refused",
            summary="negotiation dispositions are emitted by cursor-auto only",
        )
    unknown = _unknown_negotiation_fields(text)
    if unknown:
        return NegotiationParseError(
            reason="negotiation.malformed",
            summary=f"unknown negotiation fields: {', '.join(unknown)}",
        )
    for exec_field in _EXECUTION_FIELDS:
        pattern = _FIELD_RE.get(exec_field) or re.compile(
            rf"^{re.escape(exec_field)}:\s*(.+)$", re.MULTILINE | re.IGNORECASE
        )
        if pattern.search(text):
            return NegotiationParseError(
                reason="negotiation.field_refused",
                summary=f"execution field forbidden on negotiation turn: {exec_field}",
            )
    contract = (_field_value(text, "contract") or "").strip().lower()
    if contract != "confer":
        return NegotiationParseError(
            reason="negotiation.contract_refused",
            summary="negotiation requires contract: confer",
        )
    phase_raw = (_field_value(text, "negotiation_phase") or "").strip().lower()
    if phase_raw not in NEGOTIATION_PHASES:
        return NegotiationParseError(
            reason="negotiation.malformed",
            summary=f"unknown negotiation_phase: {phase_raw or '<missing>'}",
        )
    phase: NegotiationPhase = phase_raw  # type: ignore[assignment]
    negotiation_id = (_field_value(text, "negotiation_id") or "").strip()
    if not _UUID_RE.match(negotiation_id):
        return NegotiationParseError(
            reason="negotiation.malformed",
            summary="negotiation_id must be a UUID",
        )
    revision_raw = _field_value(text, "revision")
    in_reply_raw = _field_value(text, "in_reply_to_turn")
    hash_raw = (_field_value(text, "proposal_hash") or "").strip()
    idle_deadline = (_field_value(text, "idle_deadline") or "").strip()
    if revision_raw is None or in_reply_raw is None or not hash_raw or not idle_deadline:
        return NegotiationParseError(
            reason="negotiation.malformed",
            summary="missing required negotiation field",
        )
    try:
        revision = int(revision_raw)
        in_reply_to_turn = int(in_reply_raw)
    except ValueError:
        return NegotiationParseError(
            reason="negotiation.malformed",
            summary="revision and in_reply_to_turn must be integers",
        )
    if revision < 1:
        return NegotiationParseError(
            reason="negotiation.malformed",
            summary="revision must be positive",
        )
    hash_match = _HASH_RE.match(hash_raw)
    if hash_match is None:
        return NegotiationParseError(
            reason="negotiation.malformed",
            summary="proposal_hash must be sha256:<64 lowercase hex>",
        )
    proposal_hash = f"sha256:{hash_match.group(1)}"
    payload = canonical_payload_from_body(text)
    if payload is None:
        return NegotiationParseError(
            reason="negotiation.malformed",
            summary="missing canonical mission payload field",
        )
    computed = compute_proposal_hash(payload)
    if computed != proposal_hash:
        return NegotiationParseError(
            reason="negotiation.scope_refused",
            summary="proposal_hash does not match canonical payload",
        )
    if phase in {"agree", "ratify"}:
        operator = is_operator_proxy_sender(from_agent)
        if not operator:
            return NegotiationParseError(
                reason="negotiation.authority_refused",
                summary=f"{phase} requires operator-proxy sender",
            )
    if phase == "proposal" and not is_operator_proxy_sender(from_agent):
        return NegotiationParseError(
            reason="negotiation.authority_refused",
            summary="proposal requires operator-proxy sender",
        )
    if phase == "proposal" and (revision != 1 or in_reply_to_turn != 0):
        return NegotiationParseError(
            reason="negotiation.malformed",
            summary="proposal requires revision=1 and in_reply_to_turn=0",
        )
    if is_cursor_auto_sender(from_agent):
        return NegotiationParseError(
            reason="negotiation.authority_refused",
            summary="cursor-auto cannot originate negotiation requests",
        )
    return ParsedNegotiationRequest(
        phase=phase,
        negotiation_id=negotiation_id,
        revision=revision,
        in_reply_to_turn=in_reply_to_turn,
        proposal_hash=proposal_hash,
        payload=payload,
        idle_deadline=idle_deadline,
        contract=contract,
        turn_type=turn_type.upper(),
    )


def build_disposition_body(
    *,
    disposition: str,
    negotiation_id: str,
    revision: int,
    in_reply_to_turn: int,
    proposal_hash: str,
    state: NegotiationState,
    reason: str | None = None,
    agreement_ref: str | None = None,
    payload: CanonicalMissionPayload | None = None,
) -> str:
    """Render a ``TYPE: DISPOSITION`` negotiation reply body."""
    lines = [
        "TYPE: DISPOSITION",
        f"disposition: {disposition}",
        f"negotiation_id: {negotiation_id}",
        f"revision: {revision}",
        f"in_reply_to_turn: {in_reply_to_turn}",
        f"proposal_hash: {proposal_hash}",
        f"state: {state}",
    ]
    if reason:
        lines.append(f"reason: {reason}")
    if agreement_ref:
        lines.append(f"agreement_ref: {agreement_ref}")
    if payload is not None and disposition == "negotiation.countered":
        for key, value in payload.as_dict().items():
            lines.append(f"{key}: {value}")
    return "\n".join(lines) + "\n"


def field_value(body: str, name: str) -> str | None:
    """Return one negotiation field value from *body*."""
    return _field_value(body, name)


def parse_idle_deadline(value: str) -> datetime | None:
    """Parse ISO-8601 or relative ``+5m`` style idle deadlines."""
    raw = (value or "").strip()
    if not raw:
        return None
    if raw.startswith("+"):
        match = re.match(r"^\+(\d+)(m|h)$", raw)
        if match is None:
            return None
        amount = int(match.group(1))
        unit = match.group(2)
        delta = timedelta(minutes=amount) if unit == "m" else timedelta(hours=amount)
        return datetime.now(UTC) + delta
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed
