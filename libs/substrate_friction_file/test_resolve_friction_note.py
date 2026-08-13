"""Unit tests for note/claim alias resolution in substrate_friction_file."""

from __future__ import annotations

from substrate_friction_file import resolve_friction_note


def test_resolve_friction_note_claim_only() -> None:
    resolved, err = resolve_friction_note(claim="finding text")
    assert err is None
    assert resolved == "finding text"


def test_resolve_friction_note_conflict() -> None:
    resolved, err = resolve_friction_note(note="short", claim="longer finding")
    assert resolved is None
    assert err is not None
    assert "not both with different values" in err
