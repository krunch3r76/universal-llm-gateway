"""Caller-side consume router after prompt-expand produces TASK′.

Stargate and GIW prelude hooks call ``route_consume`` to choose SDK background
dispatch, in-seat tab delivery, or conductor advisory without re-running expand.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Literal

from implement_admission.prompt_expand_admit import already_expanded

_EXPAND_HEADER = "pipeline: prompt-expand"
_FIRE_HINT_LINE = re.compile(r"^\s*fire_hint:\s*(\S+)\s*$", re.IGNORECASE)

_IN_SEAT_HINTS = frozenset({"in_seat", "in-seat", "tab"})
_SDK_HINTS = frozenset({"background", "sdk"})
_CONDUCTOR_HINTS = frozenset({"conductor"})

_WINDOW_VERBS = (
    "in the window",
    "in this tab",
    "here in cursor",
    "same tab",
    "in-seat",
)
_BACKGROUND_VERBS = (
    "background",
    "while i'm away",
    "cursor-sdk",
    "sdk background",
)


class ConsumeBranch(str, Enum):
    """Post-expand delivery branch for TASK′ after prompt-expand completes.

    Prelude hooks map each branch to SDK scheduling, tab activation headers, or
    conductor advisory surfaces without changing expand authorship.
    """

    SDK_BACKGROUND = "sdk_background"
    IN_SEAT = "in_seat"
    CONDUCTOR_RECOMMEND = "conductor_recommend"


@dataclass(frozen=True, slots=True)
class ConsumeDecision:
    """Immutable consume verdict plus optional in-seat activation wire headers."""

    branch: ConsumeBranch
    reason: str
    fire_hint: str | None = None
    operator_verb: Literal["window", "background"] | None = None
    activation_header: dict[str, str] | None = None


def parse_fire_hint(task_prime: str) -> str | None:
    """Extract fire_hint from TASK′ header block (first 40 lines)."""
    for line in (task_prime or "").splitlines()[:40]:
        match = _FIRE_HINT_LINE.match(line)
        if match:
            return match.group(1).strip().lower()
    return None


def parse_operator_verb(commission: str) -> Literal["window", "background"] | None:
    """Detect attended window/background verbs in original operator commission."""
    lower = (commission or "").lower()
    for verb in _WINDOW_VERBS:
        if verb in lower:
            return "window"
    for verb in _BACKGROUND_VERBS:
        if verb in lower:
            return "background"
    return None


_SUMMON_MODE_ATTENDED = re.compile(r"(?i)summon_mode:\s*attended\b")


def parse_summon_mode_attended(text: str) -> bool:
    """True when commission or packet declares ``summon_mode: attended``."""
    return bool(_SUMMON_MODE_ATTENDED.search(text or ""))


def derive_attended(
    *,
    explicit: bool | None = None,
    transcript_id: str | None = None,
    commission_or_packet: str | None = None,
) -> bool:
    """Attended consume context from summon_mode, live transcript, or explicit flag."""
    if explicit is not None:
        return explicit
    if (transcript_id or "").strip():
        return True
    return parse_summon_mode_attended(commission_or_packet or "")


def _branch_from_fire_hint(hint: str) -> ConsumeBranch | None:
    token = hint.strip().lower().replace("-", "_")
    if token in _IN_SEAT_HINTS:
        return ConsumeBranch.IN_SEAT
    if token in _SDK_HINTS:
        return ConsumeBranch.SDK_BACKGROUND
    if token in _CONDUCTOR_HINTS:
        return ConsumeBranch.CONDUCTOR_RECOMMEND
    return None


def build_activation_envelope(
    task_prime: str,
    *,
    summoning_thread_id: str | None,
    transcript_id: str | None,
) -> dict[str, str]:
    """Wire-layer activation header for in_seat branch (not author prose)."""
    _ = task_prime  # reserved for future TASK′ digest stamps
    envelope: dict[str, str] = {
        "X-ULG-Consume-Branch": ConsumeBranch.IN_SEAT.value,
        "X-ULG-Activation-Kind": "prompt_expand_consume",
    }
    if summoning_thread_id:
        envelope["X-ULG-Summoning-Thread"] = summoning_thread_id
    if transcript_id:
        envelope["X-ULG-Transcript-Id"] = transcript_id
    return envelope


def stamp_expand_provenance(task_prime: str, expand_execution_id: str | None) -> str:
    """Ensure ``pipeline: prompt-expand`` header present (idempotent)."""
    if already_expanded(task_prime):
        return task_prime
    header_lines = ["---", _EXPAND_HEADER]
    if expand_execution_id:
        header_lines.append(f"expand_execution_id: {expand_execution_id}")
    header_lines.append("---")
    body = (task_prime or "").lstrip()
    return "\n".join(header_lines) + ("\n\n" + body if body else "")


def route_consume(
    *,
    task_prime: str,
    original_commission: str,
    attended: bool,
    durable_session: bool,
    summoning_thread_id: str | None = None,
    transcript_id: str | None = None,
) -> ConsumeDecision:
    """Apply precedence: fire_hint ≻ operator verb ≻ default SDK."""
    hint = parse_fire_hint(task_prime)
    if hint is not None:
        branch = _branch_from_fire_hint(hint)
        if branch is not None:
            activation = None
            if branch is ConsumeBranch.IN_SEAT:
                activation = build_activation_envelope(
                    task_prime,
                    summoning_thread_id=summoning_thread_id,
                    transcript_id=transcript_id,
                )
            return ConsumeDecision(
                branch=branch,
                reason=f"fire_hint:{hint}",
                fire_hint=hint,
                activation_header=activation,
            )

    verb = parse_operator_verb(original_commission)
    if verb == "background":
        return ConsumeDecision(
            branch=ConsumeBranch.SDK_BACKGROUND,
            reason="operator_verb:background",
            operator_verb=verb,
        )
    if verb == "window" and attended:
        return ConsumeDecision(
            branch=ConsumeBranch.IN_SEAT,
            reason="operator_verb:window",
            operator_verb=verb,
            activation_header=build_activation_envelope(
                task_prime,
                summoning_thread_id=summoning_thread_id,
                transcript_id=transcript_id,
            ),
        )

    if durable_session and attended and hint is None and verb is None:
        return ConsumeDecision(
            branch=ConsumeBranch.CONDUCTOR_RECOMMEND,
            reason="durable_session_attended_fallback",
        )

    return ConsumeDecision(branch=ConsumeBranch.SDK_BACKGROUND, reason="default")


__all__ = [
    "ConsumeBranch",
    "ConsumeDecision",
    "build_activation_envelope",
    "derive_attended",
    "parse_fire_hint",
    "parse_operator_verb",
    "parse_summon_mode_attended",
    "route_consume",
    "stamp_expand_provenance",
]
