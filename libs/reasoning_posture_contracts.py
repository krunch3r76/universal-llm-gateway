"""Shared contract sets for judgment-skill injection on dispatch preambles.

GIW ``resolve_prompt_preamble`` and Stargate handoff enrich both consult these
frozensets so mechanical/quick contracts skip the posture invoke while judgment
contracts — including freeform ``none`` — receive ``/reasoning-posture`` plus
the Use-line. Grok-4.7 judgment recipes use ``contract=none``; the slash is
the Cursor skill-fire cue that seat reliably honors.
"""

from __future__ import annotations

import re

from job_vocab import HARNESS_STACK_SKIP_JOBS
from job_vocab import HYPOTHESIZE_ON_JOBS as _VOCAB_HYPOTHESIZE_ON_JOBS
from job_vocab import POSTURE_SKIP_JOBS as _VOCAB_POSTURE_SKIP_JOBS

# Harvest nominates these manage slugs when this lib lands (package-grain).
CONSUMERS: tuple[str, ...] = ("git_integration_worker", "stargate")

# One set ships: production (cursor_sdk_packet, handoff_reasoning_posture)
# imports these names, and they are the job_vocab rows.
REASONING_POSTURE_SKIP_CONTRACTS = _VOCAB_POSTURE_SKIP_JOBS

# Caller prompt is sole task authority for the harness stack. Posture
# slash+Use-line still attach — freeform is judgment, not mechanical.
FREEFORM_CONTRACTS: frozenset[str] = HARNESS_STACK_SKIP_JOBS

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


# Judgment jobs that receive hypothesize-simulate rival-fill injection.
# freeform is in this set (job_vocab hypothesize_on).
HYPOTHESIZE_SIMULATE_CONTRACTS = _VOCAB_HYPOTHESIZE_ON_JOBS

# Importers outside the keep-list use these names. The Search F names stay here.
POSTURE_SKIP_JOBS = REASONING_POSTURE_SKIP_CONTRACTS
HYPOTHESIZE_ON_JOBS = HYPOTHESIZE_SIMULATE_CONTRACTS


def contract_is_freeform(contract: str | None) -> bool:
    """True when the harness must not inject steering preambles (routing / lane / identity)."""
    return (contract or "").strip().lower() in FREEFORM_CONTRACTS


__all__ = [
    "FREEFORM_CONTRACTS",
    "HYPOTHESIZE_ON_JOBS",
    "HYPOTHESIZE_SIMULATE_CONTRACTS",
    "POSTURE_SKIP_JOBS",
    "REASONING_POSTURE_PREAMBLE",
    "REASONING_POSTURE_SKIP_CONTRACTS",
    "REASONING_POSTURE_SLASH",
    "contract_is_freeform",
    "reasoning_posture_invoke_parts",
    "reasoning_posture_warrants_injection",
]
