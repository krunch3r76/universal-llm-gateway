"""Decide whether a team-dispatch admit should run prompt-expand first.

10479 caller-door bind (a:35466): night/tick work admits observe
``pipeline_id=prompt-expand`` before the seat generate fires. Fire stays on
the caller. Wake doorbells and liaison-ticker rings stay byte-identical.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

PIPELINE_ID = "prompt-expand"
_DEFAULT_ROOTS = "10479"
_WAKE_PREFIXES = ("WAKE —", "WAKE -", "WAKE ")
_EXPAND_HEADER = "pipeline: prompt-expand"
_TICKER_CALLERS = frozenset({"liaison-ticker"})
_MECHANICAL = frozenset({"wrap", "pure-mechanical", "sketch"})
_VALID_CONTRACTS = frozenset(
    {"consult", "investigate", "implement", "confer", "review", "none"}
)
_CONTRACT_ALIASES = {"conductor": "implement"}
SkipReason = Literal[
    "liaison_ticker",
    "already_expanded",
    "wake_doorbell",
    "root_not_enrolled",
    "mechanical",
    "empty_prompt",
]


@dataclass(frozen=True, slots=True)
class ExpandDecision:
    """Admit-or-skip verdict for the prompt-expand caller door."""

    admit: bool
    root: str | None = None
    skip_reason: SkipReason | None = None


def admit_roots() -> frozenset[str]:
    """Roots that opt into the caller door. Default is house 10479."""
    raw = os.environ.get("PROMPT_EXPAND_ADMIT_ROOTS", _DEFAULT_ROOTS)
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


def already_expanded(prompt: str) -> bool:
    """True when TASK' provenance header is already on the prompt."""
    head = (prompt or "")[:800]
    return _EXPAND_HEADER in head


def is_wake_prompt(prompt: str) -> bool:
    """True for liaison doorbell / successor-wake pastes (F2 byte contract)."""
    stripped = (prompt or "").lstrip()
    return stripped.startswith(_WAKE_PREFIXES)


def matching_root(
    *candidates: str | None, roots: frozenset[str] | None = None
) -> str | None:
    """First candidate that is an enrolled caller-door root."""
    enrolled = roots if roots is not None else admit_roots()
    for raw in candidates:
        token = str(raw or "").strip()
        if token and token in enrolled:
            return token
    return None


def should_expand(
    *,
    prompt: str,
    contract: str | None,
    caller_agent: str | None,
    parent_thread: str | None = None,
    dispatch_thread_id: str | None = None,
    continuity_root_thread_id: str | None = None,
    roots: frozenset[str] | None = None,
) -> ExpandDecision:
    """Return whether this admit should run prompt-expand before seat fire."""
    if str(caller_agent or "").strip() in _TICKER_CALLERS:
        return ExpandDecision(admit=False, skip_reason="liaison_ticker")
    if not (prompt or "").strip():
        return ExpandDecision(admit=False, skip_reason="empty_prompt")
    if already_expanded(prompt):
        return ExpandDecision(admit=False, skip_reason="already_expanded")
    if is_wake_prompt(prompt):
        return ExpandDecision(admit=False, skip_reason="wake_doorbell")
    root = matching_root(
        parent_thread,
        dispatch_thread_id,
        continuity_root_thread_id,
        roots=roots,
    )
    if root is None:
        return ExpandDecision(admit=False, skip_reason="root_not_enrolled")
    kind = str(contract or "none").strip() or "none"
    if kind in _MECHANICAL:
        return ExpandDecision(admit=False, root=root, skip_reason="mechanical")
    return ExpandDecision(admit=True, root=root)


def expand_contract(contract: str | None) -> str:
    """Map a generate contract onto a prompt-expand options.contract."""
    raw = str(contract or "none").strip() or "none"
    aliased = _CONTRACT_ALIASES.get(raw, raw)
    if aliased in _VALID_CONTRACTS:
        return aliased
    return "none"


def expand_stage(contract: str) -> str:
    """Default stage for the two-key profile row."""
    if contract == "implement":
        return "g5"
    if contract == "consult":
        return "g1"
    return "none"


def expand_target(*, seat: str | None, model: str | None) -> str:
    """Static v1 target: cdp vs cursor. grok-bot is a typed reject in-pipeline."""
    for raw in (seat, model):
        token = str(raw or "").strip().lower()
        if token.startswith("cdp") or token.startswith("cse"):
            return "cdp"
    return "cursor"


def expand_options(
    *,
    contract: str | None,
    seat: str | None = None,
    model: str | None = None,
) -> dict[str, str]:
    """pipeline_options for a caller-door expand run. Fire stays outside."""
    mapped = expand_contract(contract)
    return {
        "contract": mapped,
        "stage": expand_stage(mapped),
        "executor_tier": "frontier",
        "target": expand_target(seat=seat, model=model),
        "delivery": "prompt",
        "rag_fail": "stamp",
    }


__all__ = [
    "PIPELINE_ID",
    "ExpandDecision",
    "admit_roots",
    "already_expanded",
    "expand_contract",
    "expand_options",
    "expand_stage",
    "expand_target",
    "is_wake_prompt",
    "matching_root",
    "should_expand",
]
