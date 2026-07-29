"""Non-blocking briefing-target advisory for undeclared long inline turn bodies."""

from __future__ import annotations

from dataclasses import dataclass

from .turns_models import BRIEFING_TARGET_CHARS

INLINE_CONTRACT_PREFIXES = (
    "TYPE: DIRECTIVE",
    "TYPE: CLOSEOUT",
    "TYPE: CONFER",
    "TYPE: WAKE",
)

_SUGGESTION = (
    "Write substantive content to a durable sidecar (sidecar_content on send or "
    "fs write to notes/system/threads/) and post a short briefing with a pointer."
)


@dataclass(frozen=True, slots=True)
class BriefingAdvisory:
    body_chars: int
    target_chars: int
    reason: str
    suggestion: str


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


def briefing_advisory(
    *,
    body: str,
    allow_long_body: bool,
    has_sidecar: bool,
) -> BriefingAdvisory | None:
    """Return advisory when body exceeds briefing target without an exemption."""
    if len(body) <= BRIEFING_TARGET_CHARS:
        return None
    if allow_long_body or has_sidecar or _has_inline_contract_envelope(body):
        return None
    return BriefingAdvisory(
        body_chars=len(body),
        target_chars=BRIEFING_TARGET_CHARS,
        reason="over_briefing_target",
        suggestion=_SUGGESTION,
    )


__all__ = [
    "BriefingAdvisory",
    "INLINE_CONTRACT_PREFIXES",
    "briefing_advisory",
]
