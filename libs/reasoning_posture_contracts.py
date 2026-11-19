"""Contract sets and model rules for reasoning-posture injection on preambles.

GIW ``resolve_prompt_preamble`` and Stargate handoff enrich both consult these
sets. Judgment contracts, freeform included, receive ``/reasoning-posture`` plus
the Use-line; contracts in ``REASONING_POSTURE_SKIP_CONTRACTS`` skip it, except
when the caller passes a ``resolved_model`` in ``POSTURE_MANDATORY_CURSOR_MODELS``
(``composer-2.5``, ``grok-4.7``): those get the invoke and
``WHOLE_FILE_READ_BLOCK`` on every contract. Stargate handoff enrich passes no
model and keeps the contract-only rule. The slash is the Cursor skill-fire cue.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Literal

from job_vocab import HARNESS_STACK_SKIP_JOBS
from job_vocab import HYPOTHESIZE_ON_JOBS as _VOCAB_HYPOTHESIZE_ON_JOBS
from job_vocab import POSTURE_SKIP_JOBS as _VOCAB_POSTURE_SKIP_JOBS
from job_vocab.records import BY_NAME

DelegationTier = Literal["A", "B", "C"]

_CDP_ORCHESTRATOR_PURPOSES = frozenset({"operator-proxy", "mission"})
_COMPOSER_LEAF_MODELS = frozenset({"cursor/composer-2.5", "composer-2.5"})

# Harvest nominates these manage slugs when this lib lands (package-grain).
CONSUMERS: tuple[str, ...] = ("git_integration_worker", "stargate")

# One set ships: production (cursor_sdk_packet, handoff_reasoning_posture)
# imports these names, and they are the job_vocab rows.
REASONING_POSTURE_SKIP_CONTRACTS = _VOCAB_POSTURE_SKIP_JOBS

# Caller prompt is sole task authority for the harness stack. Posture
# slash+Use-line still attach — freeform is judgment, not mechanical.
FREEFORM_CONTRACTS: frozenset[str] = HARNESS_STACK_SKIP_JOBS

REASONING_POSTURE_SLASH = "/reasoning-posture"

# Use-line text injected by GIW resolve_prompt_preamble only.
# Stargate handoff enrich gates the skill slug; it does not read this string.
# Plain-language cue matching core SKILL.md (a:37289); keep the Use-line stem
# so _USE_LINE_RE de-dup still matches author-supplied copies.
REASONING_POSTURE_PREAMBLE = (
    "Use the `reasoning-posture` skill: state the question and what is out of "
    "scope, steelman before critique, label confidence, answer directly, "
    "check the source before conceding, one bounded step at a time."
)

_SLASH_LINE_RE = re.compile(r"(?m)^/reasoning-posture\s*$")
_USE_LINE_RE = re.compile(r"Use the `?reasoning-posture`? skill", re.IGNORECASE)

# Cursor bare ids that receive the posture invoke and the whole-file block on
# every contract, the skip set notwithstanding. "Admitted" (cursor_capabilities)
# and "needs posture" are different facts, so this is its own set (D5).
POSTURE_MANDATORY_CURSOR_MODELS: frozenset[str] = frozenset(
    {"composer-2.5", "grok-4.7"}
)
REASONING_POSTURE_SKILL_SLUG = "reasoning-posture"
WHOLE_FILE_READ_MARKER = "WHOLE-FILE READ (mandatory)"
WHOLE_FILE_READ_BLOCK = (
    f"{WHOLE_FILE_READ_MARKER}: Any file your output rests on, meaning a file you "
    "edit, review, cite by line or report as read, you read whole before you rely on "
    "it, at the commit or content hash the packet names when it names one. One "
    "exception: when your packet gives you a diff to review as your primary read, the "
    "diff is your read for the lines it shows, and you open a file whole wherever a "
    "judgment depends on code outside its hunks. A search hit or a partial window is a "
    "lead, not a read. When a file is longer than one read returns, read consecutive "
    "windows of at most 800 lines with no gaps, and record for each window its first "
    "line number, its last line number and the exact text of its last line. Your "
    "closeout carries a coverage table: path, content hash when the tool reports one, "
    "lines read, and whole / judged from the diff / partial / not read. Mark a file "
    "whole only when your windows run from line 1 to its last line; otherwise mark it "
    "partial and name the lines you did not read. Reads made to satisfy this rule do "
    "not count against a TIER-B read or tool-call budget; that budget still bounds "
    "exploratory search."
)
POSTURE_HARNESS_SOURCE = "giw.admit_prompt"


def posture_mandatory_model(resolved_model: str | None) -> bool:
    """True when *resolved_model* is a cursor model that always gets posture (D5).

    Strips and lower-cases first: ``canonical_cursor_bare_id`` keeps an upper-case
    provider prefix (and raises on it) and does not strip padding.
    """
    if resolved_model is None:
        return False
    text = resolved_model.strip().lower()
    if not text:
        return False
    from cursor_capabilities import canonical_cursor_bare_id

    try:
        bare = canonical_cursor_bare_id(text)
    except ValueError:
        return False
    return bare in POSTURE_MANDATORY_CURSOR_MODELS


def reasoning_posture_warrants_injection(
    contract: str | None, *, resolved_model: str | None = None
) -> bool:
    """True when the preamble should carry the posture invoke.

    A mandatory model (``posture_mandatory_model``) overrides the skip set. With no
    model the rule is contract-only, which the ``ulg-for-llms`` gate and Stargate
    handoff enrich rely on.
    """
    if posture_mandatory_model(resolved_model):
        return True
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


def posture_staging_skills(
    skills: Sequence[str] | None, *, resolved_model: str | None
) -> list[str] | None:
    """Skills to stage into the dispatch HOME: the caller's, plus the posture body
    for a mandatory model so its invoke can resolve (D10)."""
    if not posture_mandatory_model(resolved_model):
        return None if skills is None else list(skills)
    staged = list(skills or [])
    present = {str(s).strip() for s in staged}
    if (
        REASONING_POSTURE_SKILL_SLUG not in present
        and f"agent_skill:{REASONING_POSTURE_SKILL_SLUG}" not in present
    ):
        staged.append(REASONING_POSTURE_SKILL_SLUG)
    return staged


def posture_harness_report(
    prompt: str, *, resolved_model: str | None
) -> dict[str, object]:
    """What the prompt GIW assembled at admit carries (D3); a projection of that
    text, named by ``source``, not a statement about what the worker read."""
    return {
        "reasoning_posture": bool(
            _SLASH_LINE_RE.search(prompt) or _USE_LINE_RE.search(prompt)
        ),
        "whole_file_read": WHOLE_FILE_READ_MARKER in prompt,
        "mandatory_model": posture_mandatory_model(resolved_model),
        "source": POSTURE_HARNESS_SOURCE,
    }


# Judgment jobs that receive hypothesize-simulate rival-fill injection.
# freeform is in this set (job_vocab hypothesize_on).
HYPOTHESIZE_SIMULATE_CONTRACTS = _VOCAB_HYPOTHESIZE_ON_JOBS

# Importers outside the keep-list use these names. The Search F names stay here.
POSTURE_SKIP_JOBS = REASONING_POSTURE_SKIP_CONTRACTS
HYPOTHESIZE_ON_JOBS = HYPOTHESIZE_SIMULATE_CONTRACTS


def contract_is_freeform(contract: str | None) -> bool:
    """True when the harness must not inject steering preambles (routing / lane / identity)."""
    return (contract or "").strip().lower() in FREEFORM_CONTRACTS


def resolve_delegation_tier(
    contract: str | None,
    *,
    model: str | None = None,
    nest_under: str | None = None,
    purpose: str | None = None,
    lane: str | None = None,
) -> DelegationTier:
    """Map admit contract + overrides to delegation tier A/B/C (lane does not change tier)."""
    _ = lane
    if (purpose or "").strip().lower() in _CDP_ORCHESTRATOR_PURPOSES:
        return "A"
    c = (contract or "").strip().lower()
    m = (model or "").strip().lower()
    nested = bool((nest_under or "").strip())
    if c == "freeform" and (m in _COMPOSER_LEAF_MODELS or nested):
        return "C"
    record = BY_NAME.get(c)
    if record is not None:
        return record.delegation_tier
    if m in _COMPOSER_LEAF_MODELS or nested:
        return "C"
    return "B"


__all__ = [
    "DelegationTier",
    "FREEFORM_CONTRACTS",
    "HYPOTHESIZE_ON_JOBS",
    "HYPOTHESIZE_SIMULATE_CONTRACTS",
    "POSTURE_HARNESS_SOURCE",
    "POSTURE_MANDATORY_CURSOR_MODELS",
    "POSTURE_SKIP_JOBS",
    "REASONING_POSTURE_PREAMBLE",
    "REASONING_POSTURE_SKILL_SLUG",
    "REASONING_POSTURE_SKIP_CONTRACTS",
    "REASONING_POSTURE_SLASH",
    "WHOLE_FILE_READ_BLOCK",
    "WHOLE_FILE_READ_MARKER",
    "contract_is_freeform",
    "posture_harness_report",
    "posture_mandatory_model",
    "posture_staging_skills",
    "reasoning_posture_invoke_parts",
    "reasoning_posture_warrants_injection",
    "resolve_delegation_tier",
]