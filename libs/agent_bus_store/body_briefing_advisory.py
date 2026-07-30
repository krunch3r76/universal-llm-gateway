"""Non-blocking briefing-target advisory for undeclared long inline turn bodies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .checkpoint_kind_detector import (
    is_birth_shaped_checkpoint,
    is_structural_checkpoint,
)
from .checkpoint_projection import (
    authored_residue_char_count,
    is_checkpoint_subject,
)
from .turns_models import BRIEFING_TARGET_CHARS

INLINE_CONTRACT_PREFIXES = (
    "TYPE: DIRECTIVE",
    "TYPE: CLOSEOUT",
    "TYPE: CONFER",
    "TYPE: WAKE",
)

CHECKPOINT_PROFILE_SILENT_MAX = 8000
CHECKPOINT_PROFILE_SCHEMA_ADVISORY_MIN = 10000

_SIDECAR_SUGGESTION = (
    "Write substantive content to a durable sidecar (sidecar_content on send or "
    "fs write to notes/system/threads/) and post a short briefing with a pointer."
)

_SCHEMA_SHAPE_SUGGESTION = (
    "CHECKPOINT tip exceeds reconstitution-friendly size — trim narrative prose "
    "to sidecars and keep the index thin (Settled / Live / Next-pickup / RESUME "
    "footer per checkpoint-schema-profiles)."
)

AdvisoryKind = Literal[
    "default",
    "checkpoint_silent",
    "checkpoint_schema_shape",
    "birth_silent",
]


@dataclass(frozen=True, slots=True)
class BriefingAdvisory:
    body_chars: int
    target_chars: int
    reason: str
    suggestion: str
    turn_kind: str = "default"
    suppressed_by_profile: bool = False


def _first_nonempty_line(body: str) -> str:
    for line in body.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def _has_inline_contract_envelope(body: str) -> bool:
    first = _first_nonempty_line(body)
    upper = first.upper()
    return any(upper.startswith(prefix.upper()) for prefix in INLINE_CONTRACT_PREFIXES)


def _metered_body_chars(body: str, *, subject: str | None) -> int:
    if subject is not None and is_checkpoint_subject(subject):
        return authored_residue_char_count(body)
    return len(body)


def _select_advisory_profile(
    *,
    subject: str | None,
    thread_tags: list[str] | None,
    supersedes_turn: int | None,
) -> AdvisoryKind:
    if subject is None:
        return "default"
    if is_birth_shaped_checkpoint(subject=subject, supersedes_turn=supersedes_turn):
        return "birth_silent"
    if is_structural_checkpoint(
        subject=subject,
        thread_tags=thread_tags or [],
        supersedes_turn=supersedes_turn,
    ):
        return "checkpoint_silent"
    return "default"


def briefing_advisory(
    *,
    body: str,
    subject: str | None = None,
    allow_long_body: bool,
    has_sidecar: bool,
    thread_tags: list[str] | None = None,
    supersedes_turn: int | None = None,
) -> BriefingAdvisory | None:
    """Return advisory when body exceeds briefing target without an exemption."""
    profile = _select_advisory_profile(
        subject=subject,
        thread_tags=thread_tags,
        supersedes_turn=supersedes_turn,
    )
    metered = _metered_body_chars(body, subject=subject)

    if profile == "birth_silent":
        return None

    if profile == "checkpoint_silent":
        if metered <= CHECKPOINT_PROFILE_SILENT_MAX:
            return None
        if metered >= CHECKPOINT_PROFILE_SCHEMA_ADVISORY_MIN:
            return BriefingAdvisory(
                body_chars=metered,
                target_chars=CHECKPOINT_PROFILE_SCHEMA_ADVISORY_MIN,
                reason="checkpoint_schema_shape",
                suggestion=_SCHEMA_SHAPE_SUGGESTION,
                turn_kind="structural_checkpoint",
                suppressed_by_profile=False,
            )
        return None

    if metered <= BRIEFING_TARGET_CHARS:
        return None
    if allow_long_body or has_sidecar or _has_inline_contract_envelope(body):
        return None
    return BriefingAdvisory(
        body_chars=metered,
        target_chars=BRIEFING_TARGET_CHARS,
        reason="over_briefing_target",
        suggestion=_SIDECAR_SUGGESTION,
        turn_kind="default",
        suppressed_by_profile=False,
    )


__all__ = [
    "CHECKPOINT_PROFILE_SCHEMA_ADVISORY_MIN",
    "CHECKPOINT_PROFILE_SILENT_MAX",
    "BriefingAdvisory",
    "INLINE_CONTRACT_PREFIXES",
    "briefing_advisory",
]
