"""Harvest-side returned-footer validation (spec §B row 8).

Fail closed: footerless or malformed worker CHECKPOINTs are rejected before
closeout/harvest — no warn-only path, no heal/backfill.
"""

from __future__ import annotations

from universal_logging import get_logger

from .checkpoint_schema import validate_checkpoint_footer

logger = get_logger(__name__)

_MACHINE_CHECKPOINT_PREFIXES = (
    "CHECKPOINT — self-heal",
    "CHECKPOINT — consult-stall",
)


def is_machine_authored_checkpoint(subject: str | None) -> bool:
    """True when the CHECKPOINT was posted by charter-runner, not a worker seat."""
    subj = str(subject or "").strip()
    return any(subj.startswith(prefix) for prefix in _MACHINE_CHECKPOINT_PREFIXES)


def footer_field_path(checkpoint_body: str) -> tuple[bool, str]:
    """Validate returned footer; return (ok, first field path or error token)."""
    result = validate_checkpoint_footer(checkpoint_body)
    if result.ok:
        return True, ""
    return False, result.errors[0] if result.errors else "charter-state invalid"


def reject_harvest_without_footer(
    *,
    root_id: str,
    window_index: int,
    checkpoint_subject: str,
    checkpoint_body: str,
) -> bool:
    """Return True when harvest must be rejected (fail closed).

    Machine self-heal / consult-stall subjects are accepted here (detector kept
    as the P3-AC3 instrument). Callers MUST emit
    ``manage.charter.tick.harvest_footer_carveout`` on that accept branch —
    silent accept would make AC3 vacuously passable (G3b C2).
    """
    if is_machine_authored_checkpoint(checkpoint_subject):
        return False
    ok, field_path = footer_field_path(checkpoint_body)
    if ok:
        return False
    logger.error(
        "charter-runner harvest REJECTED root=%s window=%s — "
        "returned footer invalid: %s",
        root_id,
        window_index,
        field_path,
    )
    return True


__all__ = [
    "footer_field_path",
    "is_machine_authored_checkpoint",
    "reject_harvest_without_footer",
]
