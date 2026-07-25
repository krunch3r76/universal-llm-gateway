"""Pure wire-map for agent_bus.request desired_model / effort / contract.

Inline projection of dense-spec Impl §4 — no I/O. Unit-tested.
"""

from __future__ import annotations

from typing import Any, Literal

Contract = Literal["answer", "investigate", "implement", "verify"]
Disposition = Literal[
    "answered",
    "dispatched-and-relayed",
    "needs-attended",
    "declined",
]

_MODEL_TABLE: dict[str, str] = {
    "composer-2.5": "cursor/composer-2.5",
    "grok-4.5": "cursor/grok-4.5",
    "opus-5": "cursor/claude-opus-5",
}
# Canonical wire vocabulary (cursor knobs + frontier reasoning_effort).
# Aliases: CDP UI "Extra" / Cursor "Extra High" / GPT "extra-high" → xhigh.
_EFFORT_VALUES = frozenset({"low", "medium", "high", "xhigh", "max"})
_EFFORT_ALIASES: dict[str, str] = {
    "extra": "xhigh",
    "extra-high": "xhigh",
    "extra_high": "xhigh",
    "extrahigh": "xhigh",
}
_CONTRACTS = frozenset({"answer", "investigate", "implement", "verify"})


def resolve_desired_model(
    desired_model: str | None,
    *,
    contract: str = "answer",
) -> dict[str, Any]:
    """Map request ``desired_model`` hint → resolved ``model_id`` + notes.

    ``auto`` (default) picks by contract: answer→composer, investigate→grok,
    implement→opus, verify→composer. Explicit hints are honored and reported.
    """
    raw = (desired_model or "auto").strip().lower() or "auto"
    if raw == "auto":
        by_contract = {
            "answer": "cursor/composer-2.5",
            "investigate": "cursor/grok-4.5",
            "implement": "cursor/claude-opus-5",
            "verify": "cursor/composer-2.5",
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
        "investigate": "answered",
        "implement": "needs-attended",
        "verify": "answered",
    }
    return {
        "requested": raw,
        "contract": raw,
        "disposition_hint": hints[raw],
        "notes": "ok",
    }
