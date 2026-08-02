"""Pure wire-map for agent_bus.request desired_model / effort / contract.

Inline projection of dense-spec Impl §4 — no I/O. Unit-tested.
"""

from __future__ import annotations

from typing import Any, Literal

from cursor_capabilities import (
    canonical_cursor_bare_id,
    effort_knob_name,
    supported_knobs,
)

Contract = Literal[
    "answer", "confer", "investigate", "implement", "verify", "execute", "propagate", "seed"
]
Disposition = Literal[
    "answered",
    "conferred",
    "dispatched-and-relayed",
    "needs-attended",
    "declined",
    "executed",
    "propagated",
]

_MODEL_TABLE: dict[str, str] = {
    "composer-2.5": "cursor/composer-2.5",
    "grok-4.5": "cursor/grok-4.5",
    "opus-5": "cursor/claude-opus-5",
}
# Canonical wire vocabulary (cursor knobs + frontier reasoning_effort).
# Aliases: CDP UI "Extra" / Cursor "Extra High" / GPT "extra-high" → xhigh.
_EFFORT_LADDER: tuple[str, ...] = ("low", "medium", "high", "xhigh", "max")
_EFFORT_VALUES = frozenset(_EFFORT_LADDER)
_EFFORT_ALIASES: dict[str, str] = {
    "extra": "xhigh",
    "extra-high": "xhigh",
    "extra_high": "xhigh",
    "extrahigh": "xhigh",
}
_CONTRACTS = frozenset(
    {"answer", "confer", "investigate", "implement", "verify", "execute", "propagate", "seed"}
)


def resolve_desired_model(
    desired_model: str | None,
    *,
    contract: str = "answer",
) -> dict[str, Any]:
    """Map request ``desired_model`` hint → resolved ``model_id`` + notes.

    ``auto`` (default) picks by contract: answer→composer, investigate→grok,
    implement→composer, verify→composer. Explicit hints are honored and reported.
    Opus is never the auto default (lean-context implement ladder = composer).
    """
    raw = (desired_model or "auto").strip().lower() or "auto"
    if raw == "auto":
        by_contract = {
            "answer": "cursor/composer-2.5",
            "confer": "cursor/grok-4.5",
            "investigate": "cursor/grok-4.5",
            "implement": "cursor/composer-2.5",
            "verify": "cursor/composer-2.5",
            "seed": "cursor/grok-4.5",
        }
        model_id = by_contract.get(contract, "cursor/composer-2.5")
        return {
            "requested": "auto",
            "resolved_model_id": model_id,
            "honored": True,
            "notes": f"auto chose {model_id} for contract={contract}",
        }
    model_id = _MODEL_TABLE.get(raw)
    if model_id is None:
        return {
            "requested": raw,
            "resolved_model_id": "cursor/composer-2.5",
            "honored": False,
            "notes": f"unknown desired_model={raw!r}; clamped to composer-2.5",
        }
    knobs: dict[str, str] = {}
    if model_id == "cursor/claude-opus-5":
        knobs = {"thinking": "true"}
    return {
        "requested": raw,
        "resolved_model_id": model_id,
        "honored": True,
        "model_knobs": knobs,
        "notes": "honored explicit desired_model",
    }


def resolve_desired_effort(desired_effort: str | None) -> dict[str, Any]:
    """Normalize + clamp ``desired_effort`` to canonical wire values.

    Canonical set: low|medium|high|xhigh|max. Surface aliases (``extra``,
    ``extra-high``, ``Extra High``) normalize to ``xhigh`` before clamp.
    """
    requested = (desired_effort or "medium").strip().lower() or "medium"
    # Spaces → hyphens so "extra high" / "Extra High" share one alias key.
    key = requested.replace(" ", "-")
    normalized = _EFFORT_ALIASES.get(key, key)
    if normalized in _EFFORT_VALUES:
        notes = "honored"
        if normalized != key:
            notes = f"normalized {requested!r}→{normalized}"
        return {
            "requested": requested,
            "resolved_effort": normalized,
            "clamped": False,
            "notes": notes,
        }
    return {
        "requested": requested,
        "resolved_effort": "medium",
        "clamped": True,
        "notes": f"unknown desired_effort={requested!r}; clamped to medium",
    }


def resolve_contract_disposition(contract: str | None) -> dict[str, Any]:
    """Map request.contract → Auto disposition guidance (pure)."""
    raw = (contract or "answer").strip().lower() or "answer"
    if raw not in _CONTRACTS:
        return {
            "requested": raw,
            "contract": "answer",
            "disposition_hint": "answered",
            "notes": f"unknown contract={raw!r}; treated as answer",
        }
    hints: dict[str, Disposition] = {
        "answer": "answered",
        "confer": "conferred",
        "investigate": "dispatched-and-relayed",
        "implement": "dispatched-and-relayed",
        "verify": "dispatched-and-relayed",
        "execute": "executed",
        "propagate": "propagated",
        "seed": "dispatched-and-relayed",
    }
    return {
        "requested": raw,
        "contract": raw,
        "disposition_hint": hints[raw],
        "notes": "ok",
    }


def _clamp_effort_to_accepted(requested: str, accepted: tuple[str, ...]) -> str | None:
    """Pick the highest accepted rung at or below *requested* on the effort ladder.

    Descriptors disagree on ceiling (grok tops at ``high``, opus at ``max``), so a
    verbatim mismatch must degrade rather than drop the knob — dropping silently
    hands the bridge a model default that can be far above what was asked for.
    """
    if requested in accepted:
        return requested
    ladder = _EFFORT_LADDER
    if requested not in ladder:
        return None
    in_ladder = [value for value in ladder if value in accepted]
    if not in_ladder:
        return None
    below = [value for value in in_ladder if ladder.index(value) <= ladder.index(requested)]
    return below[-1] if below else in_ladder[0]


def compose_model_knobs(
    model: dict[str, Any],
    effort: dict[str, Any],
) -> dict[str, str]:
    """Merge the resolved effort onto a model's knobs, capability-clamped.

    ``resolve_desired_model`` only carries model-intrinsic knobs (opus thinking);
    without this merge the resolved effort is reported on the admit turn but never
    reaches the bridge, so every Auto-bound reasoner ran at its catalog default.
    """
    knobs: dict[str, str] = dict(model.get("model_knobs") or {})
    model_id = str(model.get("resolved_model_id") or "").strip()
    requested = str(effort.get("resolved_effort") or "").strip().lower()
    if not model_id or not requested:
        return knobs
    try:
        bare = canonical_cursor_bare_id(model_id)
    except ValueError:
        return knobs
    name = effort_knob_name(bare)
    if name is None:
        return knobs
    spec = supported_knobs(bare).get(name)
    if spec is None:
        return knobs
    value = _clamp_effort_to_accepted(requested, tuple(spec.accepted))
    if value is not None:
        knobs[name] = value
    return knobs


def resolve_handoff_contract(contract: str | None) -> str:
    """Map Auto ``request.contract`` → cursor-sdk ``handoff_contract``."""
    raw = (contract or "answer").strip().lower() or "answer"
    if raw == "implement":
        return "pure-mechanical"
    if raw == "execute":
        return "light-bounded"
    if raw == "propagate":
        return "light-bounded"
    if raw in {"investigate", "confer", "seed"}:
        return "light-bounded"
    if raw == "verify":
        return "light-bounded"
    return "light-bounded"
