"""Shared contract sets for judgment-skill injection on dispatch preambles.

GIW ``resolve_prompt_preamble`` and Stargate handoff enrich both consult these
frozensets so mechanical/quick contracts skip the posture invoke while judgment
contracts — including freeform ``none`` — receive ``/reasoning-posture`` plus
the Use-line. Grok-4.7 judgment recipes use ``contract=none``; the slash is
the Cursor skill-fire cue that seat reliably honors.
"""

from __future__ import annotations

import re

# Harvest nominates these manage slugs when this lib lands (package-grain).
CONSUMERS: tuple[str, ...] = ("git_integration_worker", "stargate")

REASONING_POSTURE_SKIP_CONTRACTS = frozenset(
    {"implement", "pure-mechanical", "propagate", "execute", "answer", "ask"}
)

# ``team_dispatch(contract="none")``: caller prompt is sole task authority for
# the harness stack (routing, lane-B, conductor identity). Posture slash+Use-line
# still attach — ``none`` is judgment, not mechanical.
FREEFORM_CONTRACTS: frozenset[str] = frozenset({"none"})

REASONING_POSTURE_SLASH = "/reasoning-posture"

# Shared Use-line for GIW preamble, Stargate handoff enrich, and cursor-auto admit.
REASONING_POSTURE_PREAMBLE = (
    "Use the `reasoning-posture` skill — pin Question/OOS/detent before merits; "
    "steelman / calibrate / courage; thinking_off does not waive."
)

_SLASH_LINE_RE = re.compile(r"(?m)^/reasoning-posture\s*$")
_USE_LINE_RE = re.compile(r"Use the `?reasoning-posture`? skill", re.IGNORECASE)


def reasoning_posture_warrants_injection(contract: str | None) -> bool:
    """True when *contract* should receive the judgment posture invoke."""
    return (contract or "").strip().lower() not in REASONING_POSTURE_SKIP_CONTRACTS


def reasoning_posture_invoke_parts(*texts: str | None) -> tuple[str, ...]:
    """Slash first (Cursor / Grok skill fire), then Use-line. Skip cues already present."""
    blob = "\n".join(t for t in texts if t)
    parts: list[str] = []
    if not _SLASH_LINE_RE.search(blob):
        parts.append(REASONING_POSTURE_SLASH)
    if not _USE_LINE_RE.search(blob):
        parts.append(REASONING_POSTURE_PREAMBLE)
    return tuple(parts)


# Judgment contracts that receive hypothesize-simulate rival-fill injection.
HYPOTHESIZE_SIMULATE_CONTRACTS = frozenset({"consult", "sketch", "conductor"})


def contract_is_freeform(contract: str | None) -> bool:
    """True when the harness must not inject steering preambles (routing / lane / identity)."""
    return (contract or "").strip().lower() in FREEFORM_CONTRACTS


__all__ = [
    "FREEFORM_CONTRACTS",
    "HYPOTHESIZE_SIMULATE_CONTRACTS",
    "REASONING_POSTURE_PREAMBLE",
    "REASONING_POSTURE_SKIP_CONTRACTS",
    "REASONING_POSTURE_SLASH",
    "contract_is_freeform",
    "reasoning_posture_invoke_parts",
    "reasoning_posture_warrants_injection",
]
