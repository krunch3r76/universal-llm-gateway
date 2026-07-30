"""Fail-closed CHECKPOINT admit gate for charter-runner tick roots.

Pure validation over a CHECKPOINT body. Used by eligibility (tick skip reasons)
and by seats/CLIs before claiming tick-ready. **Typed admit (R2):** when the
ledger row is valid, CHECKPOINT tip is optional resume/audit — not admit SOT.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .checkpoint_schema import ParsedCheckpoint, parse_checkpoint, split_sections
from .root_ledger import RootLedgerRow, typed_record_valid
from .window_terminal_contract import (
    admitted_arc,
    arc_is_weaker_than,
    density_triage_from_pickup,
    effective_required_arc,
    todo_refs_for_arc,
)

# Required ## headings (substring match via split_sections keys).
_REQUIRED_SECTION_NEEDLES: tuple[tuple[str, str], ...] = (
    ("steps", "Add `## Steps` with glyph list (`[ ]`/`[~]`/`[x]`)."),
    (
        "next pickup",
        "Add `## Next pickup` (not bold `**Next pickup:**`) with gated G/R rows.",
    ),
    ("wip", "Add `## In-flight / WIP` (or `## WIP`) with `none` when idle."),
    ("frictions", "Add `## Frictions` — list or `_None this window._`."),
    ("sidecars", "Add `## Sidecars` — list or `_None this window._`."),
)

_FIX_HINTS: dict[str, str] = {
    "parse_failed": "CHECKPOINT body failed parse — use ## sections per tick_charter schema.",
    "blocked": "Clear BLOCKED / blocked steps before expect tick admit.",
    "wip_active": "Set `## In-flight / WIP` to `none` (not bold Live: WIP=none).",
    "operator_fork": "Resolve [await:operator] / OPERATOR: forks before tick admit.",
    "no_gated_pickup": (
        "Put gated G/R ids under `## Next pickup` (## heading required — "
        "bold labels yield empty pickup)."
    ),
    "missing_resume_footer": (
        "Append canonical RESUME footer starting with "
        "`— RESUME (any seat, no command):`."
    ),
    "missing_sections": "Add required ## Steps, Next pickup, WIP, Frictions, Sidecars.",
}

# Schema-class skips eligible for machine self-heal (not window_in_flight-only).
SCHEMA_REASONS = frozenset(
    {
        "missing_sections",
        "missing_resume_footer",
        "no_gated_pickup",
        "parse_failed",
    }
)


@dataclass(frozen=True)
class CheckpointAdmitVerdict:
    """Machine-readable admit check + one-line fix hint for cursor."""

    ok: bool
    reason: str
    fix_hint: str
    parsed: ParsedCheckpoint | None = None
    missing_sections: tuple[str, ...] = ()


def _section_keys(body: str) -> set[str]:
    return set(split_sections(body or {}).keys())


def _missing_required_sections(body: str) -> list[str]:
    keys = _section_keys(body)
    missing: list[str] = []
    for needle, _hint in _REQUIRED_SECTION_NEEDLES:
        if needle == "wip":
            if not any(
                "wip" in k or "in-flight" in k or "in flight" in k for k in keys
            ):
                missing.append("wip")
            continue
        if needle == "next pickup":
            if not any("next pickup" in k or "next-pickup" in k for k in keys):
                missing.append("next pickup")
            continue
        if not any(needle in k for k in keys):
            missing.append(needle)
    return missing


def _hint_for_missing(missing: list[str]) -> str:
    hints = []
    for name, hint in _REQUIRED_SECTION_NEEDLES:
        if name in missing:
            hints.append(hint)
    return " ".join(hints) if hints else _FIX_HINTS["missing_sections"]


def validate_checkpoint_for_admit(
    body: str,
    *,
    require_schema: bool = True,
    conveyor_phase: str | None = None,
) -> CheckpointAdmitVerdict:
    """Validate a tick_charter CHECKPOINT body for admit eligibility.

    ``require_schema`` adds RESUME footer + required ## sections (fail-closed
    recurrence guard). Cap / env predicates stay in eligibility.
    """
    try:
        parsed = parse_checkpoint(body or "")
    except Exception:  # noqa: BLE001 — never raise into tick
        return CheckpointAdmitVerdict(
            False,
            "parse_failed",
            _FIX_HINTS["parse_failed"],
        )

    if require_schema:
        missing = _missing_required_sections(body or "")
        if missing:
            return CheckpointAdmitVerdict(
                False,
                "missing_sections",
                _hint_for_missing(missing),
                parsed=parsed,
                missing_sections=tuple(missing),
            )
        if not parsed.has_resume_footer:
            return CheckpointAdmitVerdict(
                False,
                "missing_resume_footer",
                _FIX_HINTS["missing_resume_footer"],
                parsed=parsed,
            )

    if parsed.blocked:
        return CheckpointAdmitVerdict(
            False, "blocked", _FIX_HINTS["blocked"], parsed=parsed
        )
    if not parsed.wip_is_none:
        return CheckpointAdmitVerdict(
            False, "wip_active", _FIX_HINTS["wip_active"], parsed=parsed
        )
    if parsed.open_operator_fork:
        return CheckpointAdmitVerdict(
            False, "operator_fork", _FIX_HINTS["operator_fork"], parsed=parsed
        )
    if parsed.consult_pending:
        return CheckpointAdmitVerdict(True, "consult_pending", "", parsed=parsed)
    if not parsed.next_pickup_gated:
        if conveyor_phase == "dormant":
            return CheckpointAdmitVerdict(
                True, "dormant_update", "", parsed=parsed
            )
        return CheckpointAdmitVerdict(
            False,
            "no_gated_pickup",
            _FIX_HINTS["no_gated_pickup"],
            parsed=parsed,
        )
    return CheckpointAdmitVerdict(True, "eligible", "", parsed=parsed)


def validate_admit_eligibility(
    body: str,
    *,
    ledger_row: RootLedgerRow | None = None,
    require_schema: bool = True,
    conveyor_phase: str | None = None,
) -> CheckpointAdmitVerdict:
    """Typed-first admit check — tip optional when ledger row is valid."""
    if typed_record_valid(ledger_row):
        parsed = None
        if body:
            try:
                parsed = parse_checkpoint(body)
            except Exception:  # noqa: BLE001 — malformed tip does not block typed admit
                parsed = None
        return CheckpointAdmitVerdict(True, "typed_admit", "", parsed=parsed)
    return validate_checkpoint_for_admit(
        body,
        require_schema=require_schema,
        conveyor_phase=conveyor_phase,
    )


def validate_arc_for_admit(
    parsed: ParsedCheckpoint,
    *,
    window_kind: str,
    admission_mode: str,
    consult_role: str | None,
    executor_lane: str,
    checkpoint_body: str = "",
    density_triage_lookup: Callable[[str], str | None] | None = None,
) -> CheckpointAdmitVerdict | None:
    """Refuse when the admit lane is weaker than the row's derived arc."""
    lookup = density_triage_lookup
    if lookup is None:
        from .window_terminal_contract import default_density_triage_lookup as lookup
    refs = todo_refs_for_arc(parsed)
    if not refs:
        return None
    triage = density_triage_from_pickup(parsed)
    if triage is None:
        triage = lookup(refs[0])
    g_row_lane = parsed.executor_lane or executor_lane
    need = effective_required_arc(
        triage=triage,
        executor_lane=g_row_lane,
        consult_pending=parsed.consult_pending,
        checkpoint_body=checkpoint_body,
        parsed=parsed,
    )
    got = admitted_arc(
        window_kind=window_kind,
        admission_mode=admission_mode,
        consult_role=consult_role,
        executor_lane=executor_lane,
        parsed=parsed,
    )
    if not arc_is_weaker_than(got, need):
        return None
    hint = (
        f"G-row requires {need} arc (density_triage={triage or 'unset'}) — "
        f"admit {need} consult/recon seat, not {got} in-seat lane."
    )
    return CheckpointAdmitVerdict(False, "arc_lane_too_weak", hint, parsed=parsed)


__all__ = [
    "CheckpointAdmitVerdict",
    "SCHEMA_REASONS",
    "validate_admit_eligibility",
    "validate_arc_for_admit",
    "validate_checkpoint_for_admit",
]
