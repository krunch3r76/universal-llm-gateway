"""Tier resolution helpers for grokbuild dispatch.

Extracted from dispatch.py to keep that module under the 300 SLOC
ceiling; these are pure-function utilities with no I/O side effects.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from grokbuild.constants import (
    _TIER_PRESETS,
    DEFAULT_TIMEOUT_SECONDS,
)


def _resolve_check(
    *,
    mode: Literal["read_only", "edit"],
    explicit: bool | None,
) -> bool:
    """Mode-aware check resolution.

    Explicit True/False wins. None resolves to True for mode='edit',
    False for mode='read_only' (per plan §TIER × MODE × CHECK).
    """
    if explicit is not None:
        return explicit
    return mode == "edit"


@dataclass(frozen=True, slots=True)
class _ResolvedParams:
    """Tier-overlay + explicit-override resolution output.

    Every field is a concrete scalar. None on reasoning_effort/effort/
    max_turns/best_of_n means "do not emit the corresponding grok CLI
    flag at all" (caller chose to skip explicitly).
    """

    tier: str
    reasoning_effort: str
    effort: str
    timeout_seconds: int | None
    check: bool
    max_turns: int | None
    best_of_n: int | None


def _resolve_params(
    *,
    tier: str,
    reasoning_effort: str | None,
    effort: str | None,
    timeout_seconds: int | None,
    check: bool | None,
    max_turns: int | None,
    best_of_n: int | None,
    mode: Literal["read_only", "edit"],
) -> _ResolvedParams:
    """Apply tier preset, then per-param explicit overrides.

    Caller responsibility: ``tier`` MUST be in _TIER_PRESETS. ``dispatch_op``
    enforces this pre-resolve so a bad tier produces the structured
    rejected envelope rather than a KeyError; direct callers (tests) must
    pre-validate. reasoning_effort/effort: explicit value wins over preset.
    timeout_seconds: explicit int wins; omitted None → DEFAULT_TIMEOUT_SECONDS;
    0 → None (no wall-clock limit). max_turns/best_of_n: opt-in only;
    explicit None means "do not include the grok flag".
    """
    preset = _TIER_PRESETS[tier]
    return _ResolvedParams(
        tier=tier,
        reasoning_effort=reasoning_effort
        if reasoning_effort is not None
        else preset.reasoning_effort,
        effort=effort if effort is not None else preset.effort,
        timeout_seconds=(
            None
            if timeout_seconds == 0
            else (
                timeout_seconds
                if timeout_seconds is not None
                else DEFAULT_TIMEOUT_SECONDS
            )
        ),
        check=_resolve_check(mode=mode, explicit=check),
        max_turns=max_turns,
        best_of_n=best_of_n,
    )
