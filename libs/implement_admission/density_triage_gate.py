"""Implement-lane density_triage vocabulary for todo attrs and gate errors."""

from __future__ import annotations

_TRIAGE_EFFECTS: tuple[tuple[str, str], ...] = (
    ("mechanical", "bypass implement-ready gates"),
    (
        "judgment_required",
        "implement_ready assertion required; axis-2 only when "
        "check_requested=true (else skip / recon_waived / skeptic_ratified)",
    ),
    ("recon_pending", "blocked until re-triage after two-axis recon"),
)

MECHANICAL, JUDGMENT_REQUIRED, RECON_PENDING = tuple(name for name, _ in _TRIAGE_EFFECTS)

IMPLEMENT_GATE_TRIAGE = frozenset({MECHANICAL, JUDGMENT_REQUIRED, RECON_PENDING})

_CHECK_REQUESTED_TRUE = frozenset({"1", "true", "yes", "on"})


def check_requested_bool(raw: object) -> bool:
    """Opt-in axis-2 / Gate-6 ratification. Absent or falsey ⇒ not requested."""
    if raw is True or raw is False:
        return raw
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return raw == 1
    if raw is None:
        return False
    text = str(raw).strip().lower()
    if not text:
        return False
    return text in _CHECK_REQUESTED_TRUE


def format_implement_gate_triage_catalog() -> str:
    return "; ".join(f"{name} ({effect})" for name, effect in _TRIAGE_EFFECTS)


def format_implement_triage_unknown_reason(
    todo_id: str,
    density_triage: str | None,
) -> str:
    triage = (density_triage or "").strip() or None
    label = "unset" if triage is None else repr(triage)
    return (
        f"{todo_id}: density_triage is {label} — accepted todo values: "
        f"{format_implement_gate_triage_catalog()}. "
        "Densify via a reasoning tier before implement dispatch when unset."
    )
