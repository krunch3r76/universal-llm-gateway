"""Canonical §2 closeout field vocabulary — one generator for parser and prompt.

The relay projector matches these tokens literally. Any prompt surface that tells
an executor which fields to emit MUST be generated from this tuple, so the surface
the executor reads cannot drift from the surface the parser matches.
"""

from __future__ import annotations

SECTION2_FIELDS: tuple[tuple[str, str], ...] = (
    ("status", "status"),
    ("ac_verdict", "ac_verdict"),
    ("deltas_to_spec", "deltas_to_spec"),
    ("decisions_taken", "decisions_taken"),
    ("effects", "effects"),
    ("evidence", "evidence"),
    ("next", "next"),
    ("open forks", "open forks"),
    ("access", "access"),
    ("coverage", "coverage"),
    ("model_actual", "model_actual"),
    ("checkpoint_claim", "checkpoint_claim"),
)


def section2_field_names() -> tuple[str, ...]:
    """Return the literal heading tokens the projector matches."""
    return tuple(field for field, _label in SECTION2_FIELDS)


def section2_emit_line() -> str:
    """Return the prompt line enumerating every §2 field as a literal heading."""
    names = ", ".join(f"`{name}`" for name in section2_field_names())
    return (
        "Emit §2 fields inline in your closeout, one per field, using these "
        f"headings verbatim — no paraphrase, no semantic rename: {names}. "
        "A field you have nothing to report for still gets its heading with an "
        "explicit negative answer."
    )


__all__ = ["SECTION2_FIELDS", "section2_emit_line", "section2_field_names"]
