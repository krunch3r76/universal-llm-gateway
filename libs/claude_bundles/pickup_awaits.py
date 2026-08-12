"""Fail-closed pickup declaration + cease-to-act gate (sibling of mission_close_wake).

Orphan class (arc cdp-operator-integration-restructure / 6655#1809): a decision
bind was posted mid-stream and the authoring seat ceased acting without a
commission. ``mission_close_wake`` only fires on ``TYPE: MISSION_CLOSEOUT`` and
would not have caught a mid-episode park/idle.

Fork binds (operator 6885):
  1. Explicit ``pickup:`` / ``fyi:`` tokens — never imply awaits from a TYPE.
  2. Refuse-stop — never auto-commission.

Thin surface: declaration on architecture-bind posts; refuse at cease-to-act
(PARKED, MISSION_CLOSEOUT, CONTINUITY_HANDOFF, DISPOSITION) when unbound
``pickup:`` turns remain on the lane. Silence/idle with no bus write is
unreachable from send/reply — stated in the deliverable sidecar.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from agent_bus_store.disposition import body_has_disposition_type

from claude_bundles.cse_session_common import is_parked_body
from claude_bundles.mission_close_wake import is_mission_closeout

# Wake-token colon grammar (same shape as collector:/followup:/…).
_PICKUP_TOKEN_RE = re.compile(r"(?i)\b(pickup|awaits_commission)\s*:")
_FYI_TOKEN_RE = re.compile(r"(?i)\bfyi\s*:")
_ARCH_BIND_SUBJECT_RE = re.compile(r"(?i)\bARCHITECTURE\s+BIND\b")
_ARCH_BIND_TYPE_RE = re.compile(r"(?i)^TYPE:\s*ARCHITECTURE\s+BIND\b", re.M)
_CONTINUITY_TYPE_RE = re.compile(r"(?i)^TYPE:\s*CONTINUITY_HANDOFF\b", re.M)
_STATUS_WRAPPER_RE = re.compile(r"(?i)^(status|DIRECTIVE|CLOSEOUT)\b")

_OFFENDING_ITEM_MAX = 200

PICKUP_DECLARATION_FIX_HINT = (
    "Architecture-bind posts must declare intent with a wake-grammar token: "
    "`pickup: <seat>` (or `awaits_commission: true`) when the bind awaits a "
    "commission, or `fyi: <note>` when it does not. Example: "
    "`pickup: cursor-auto`. The substrate will not infer awaits from the "
    "ARCHITECTURE BIND type alone."
)

PICKUP_AWAITS_STOP_FIX_HINT = (
    "Cease-to-act refused — unbound `pickup:` / `awaits_commission:` turn(s) "
    "remain on this lane. Before PARKED / MISSION_CLOSEOUT / CONTINUITY_HANDOFF "
    "/ DISPOSITION: fire the commission (agent_bus.request DIRECTIVE) and cite "
    "the pickup turn (`6655#1809`, `t1809`, `turn: 1809`), or edit the pickup "
    "turn to `fyi:` if it was never awaiting action. Refuse-stop is deliberate; "
    "there is no auto-commission."
)


@dataclass(frozen=True, slots=True)
class PickupAwaitsVerdict:
    """Result of pickup declaration or cease-to-act validation."""

    ok: bool
    reason: str | None = None
    missed_tokens: tuple[str, ...] = ()
    fix_hint: str = PICKUP_DECLARATION_FIX_HINT


@dataclass(frozen=True, slots=True)
class PriorTurn:
    """Minimal prior-turn view for unbound-pickup scanning."""

    turn_number: int
    subject: str = ""
    body: str = ""


def has_pickup_declaration(text: str) -> bool:
    """True when body/subject carries ``pickup:`` or ``awaits_commission:``."""
    return bool(_PICKUP_TOKEN_RE.search(text or ""))


def has_fyi_declaration(text: str) -> bool:
    """True when body/subject carries ``fyi:`` (explicit non-await)."""
    return bool(_FYI_TOKEN_RE.search(text or ""))


def is_architecture_bind_post(*, subject: str = "", body: str = "") -> bool:
    """True for author architecture-bind speech acts (Class A / 6655#1809 shape).

    Matches subject ``ARCHITECTURE BIND`` or body ``TYPE: ARCHITECTURE BIND``.
    Excludes ``status:`` / ``DIRECTIVE`` / ``CLOSEOUT`` subject wrappers so
    admit/harvest wrappers are not forced to re-declare.
    """
    subj = (subject or "").strip()
    if subj and _STATUS_WRAPPER_RE.match(subj):
        return False
    if _ARCH_BIND_SUBJECT_RE.search(subj):
        return True
    return bool(_ARCH_BIND_TYPE_RE.search(body or ""))


def is_cease_to_act(*, subject: str = "", body: str = "") -> bool:
    """True when the turn is a seat ceasing to act on the lane.

    Covers ``TYPE: PARKED``, mission closeout, continuity handoff, and
    ``TYPE: DISPOSITION`` (leg boundary / walk-away). Silence with no bus write
    is not observable here.
    """
    if is_parked_body(body or ""):
        return True
    if is_mission_closeout(subject=subject, body=body):
        return True
    text = body or ""
    if _CONTINUITY_TYPE_RE.search(text):
        return True
    if body_has_disposition_type(text):
        return True
    return False


def validate_pickup_declaration(
    *,
    subject: str = "",
    body: str = "",
) -> PickupAwaitsVerdict:
    """Refuse architecture-bind posts that omit ``pickup:``/``fyi:`` intent."""
    if not is_architecture_bind_post(subject=subject, body=body):
        return PickupAwaitsVerdict(ok=True)
    blob = f"{subject or ''}\n{body or ''}"
    if has_pickup_declaration(blob) or has_fyi_declaration(blob):
        return PickupAwaitsVerdict(ok=True)
    return PickupAwaitsVerdict(
        ok=False,
        reason="pickup_declaration_missing",
        missed_tokens=("pickup: <seat>|awaits_commission: true|fyi: <note>",),
        fix_hint=PICKUP_DECLARATION_FIX_HINT,
    )


def _truncate(text: str, *, limit: int = _OFFENDING_ITEM_MAX) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "…"


def format_unbound_pickup_labels(
    unbound: Sequence[PriorTurn],
) -> tuple[str, ...]:
    """Human labels for unbound ``pickup:`` turns — shared by cease-refuse and quiet alarm."""
    return tuple(
        _truncate(f"t{t.turn_number}: {t.subject or '(no subject)'}") for t in unbound
    )


def _cites_turn(text: str, turn_number: int) -> bool:
    """True when text cites ``turn_number`` in the commission-ref family."""
    n = int(turn_number)
    patterns = (
        rf"#{n}\b",
        rf"\bt{n}\b",
        rf"\bturn\s*[:=]?\s*{n}\b",
        rf"\b\d+#{n}\b",
        rf"\bagent-bus\s*[:#]\s*\d+\s*#?\s*{n}\b",
    )
    blob = text or ""
    return any(re.search(p, blob, re.I) for p in patterns)


def find_unbound_pickup_turns(
    prior_turns: Sequence[PriorTurn | Mapping[str, object]],
    *,
    closing_text: str = "",
) -> tuple[PriorTurn, ...]:
    """Return prior turns that declared pickup and lack a later commission cite."""
    normalized: list[PriorTurn] = []
    for raw in prior_turns:
        if isinstance(raw, PriorTurn):
            normalized.append(raw)
            continue
        try:
            tn = int(raw.get("turn_number") or raw.get("turn") or 0)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if tn <= 0:
            continue
        normalized.append(
            PriorTurn(
                turn_number=tn,
                subject=str(raw.get("subject") or ""),
                body=str(raw.get("body") or ""),
            )
        )
    normalized.sort(key=lambda t: t.turn_number)
    unbound: list[PriorTurn] = []
    for turn in normalized:
        blob = f"{turn.subject}\n{turn.body}"
        if not has_pickup_declaration(blob):
            continue
        # Later turns (and the cease body itself) may discharge via cite.
        later = [
            t
            for t in normalized
            if t.turn_number > turn.turn_number
        ]
        discharged = any(
            _cites_turn(f"{t.subject}\n{t.body}", turn.turn_number) for t in later
        ) or _cites_turn(closing_text, turn.turn_number)
        if not discharged:
            unbound.append(turn)
    return tuple(unbound)


def validate_pickup_awaits_on_cease(
    *,
    subject: str = "",
    body: str = "",
    prior_turns: Sequence[PriorTurn | Mapping[str, object]] | None = None,
) -> PickupAwaitsVerdict:
    """Refuse cease-to-act while unbound ``pickup:`` turns remain on the lane."""
    if not is_cease_to_act(subject=subject, body=body):
        return PickupAwaitsVerdict(ok=True)
    if prior_turns is None:
        # Caller could not supply history — do not guess; declaration gate
        # still covers new architecture binds. Park-with-history is the
        # stop-time path when prior_turns is loaded.
        return PickupAwaitsVerdict(ok=True)
    closing = f"{subject or ''}\n{body or ''}"
    unbound = find_unbound_pickup_turns(prior_turns, closing_text=closing)
    if not unbound:
        return PickupAwaitsVerdict(ok=True)
    labels = format_unbound_pickup_labels(unbound)
    return PickupAwaitsVerdict(
        ok=False,
        reason="pickup_awaits_unbound",
        missed_tokens=labels,
        fix_hint=PICKUP_AWAITS_STOP_FIX_HINT,
    )


def validate_pickup_awaits(
    *,
    subject: str = "",
    body: str = "",
    prior_turns: Sequence[PriorTurn | Mapping[str, object]] | None = None,
) -> PickupAwaitsVerdict:
    """Run declaration then cease-to-act checks (single entry for call sites)."""
    declared = validate_pickup_declaration(subject=subject, body=body)
    if not declared.ok:
        return declared
    return validate_pickup_awaits_on_cease(
        subject=subject,
        body=body,
        prior_turns=prior_turns,
    )


def refusal_envelope(verdict: PickupAwaitsVerdict) -> dict[str, object]:
    """Structured MCP/cursor-auto refusal payload (missed_tokens + fix_hint)."""
    reason = verdict.reason or "pickup_declaration_missing"
    if reason == "pickup_awaits_unbound":
        error = (
            "Cease-to-act refused — unbound pickup declaration(s) on this lane "
            f"({reason})."
        )
    else:
        error = (
            "Architecture bind refused — missing pickup/fyi declaration "
            f"({reason})."
        )
    return {
        "error": error,
        "reason": reason,
        "missed_tokens": list(verdict.missed_tokens),
        "fix_hint": verdict.fix_hint,
        "status": "blocked",
    }


def coerce_prior_turns(
    rows: Iterable[Mapping[str, object] | PriorTurn] | None,
) -> list[PriorTurn]:
    """Coerce relay turn dicts or ``PriorTurn`` rows for cease scanning."""
    if not rows:
        return []
    out: list[PriorTurn] = []
    for raw in rows:
        if isinstance(raw, PriorTurn):
            out.append(raw)
            continue
        try:
            tn = int(raw.get("turn_number") or 0)
        except (TypeError, ValueError):
            continue
        if tn <= 0:
            continue
        out.append(
            PriorTurn(
                turn_number=tn,
                subject=str(raw.get("subject") or ""),
                body=str(raw.get("body") or ""),
            )
        )
    return out


__all__ = [
    "PICKUP_AWAITS_STOP_FIX_HINT",
    "PICKUP_DECLARATION_FIX_HINT",
    "PickupAwaitsVerdict",
    "PriorTurn",
    "coerce_prior_turns",
    "find_unbound_pickup_turns",
    "has_fyi_declaration",
    "has_pickup_declaration",
    "is_architecture_bind_post",
    "is_cease_to_act",
    "refusal_envelope",
    "validate_pickup_awaits",
    "validate_pickup_awaits_on_cease",
    "validate_pickup_declaration",
]
