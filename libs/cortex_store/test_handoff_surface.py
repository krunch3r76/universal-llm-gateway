"""Read-side handoff surface-but-flag consumer (agent-bus thread 1188)."""

from __future__ import annotations

from cortex_store.handoff_derivation import (
    DERIVATION_DETACHED_STRING,
    DERIVATION_SECTION,
    DERIVATION_SECTION_AMBIGUOUS,
)
from cortex_store.handoff_surface import (
    apply_handoff_read_projection,
    build_handoff_surface,
    effective_handoff_derivation,
    handoff_surface_action_hints,
)


def test_effective_derivation_defaults_legacy_to_detached() -> None:
    assert effective_handoff_derivation(None) == DERIVATION_DETACHED_STRING
    assert effective_handoff_derivation({}) == DERIVATION_DETACHED_STRING


def test_verified_section_with_source_file() -> None:
    surface = build_handoff_surface(
        {
            "handoff_prompt": "Continue from marker.",
            "handoff_provenance": {
                "derivation": DERIVATION_SECTION,
                "source_file": "notes/system/sessions/h.md",
                "source_file_sha256": "sha256:abc",
            },
        }
    )
    assert surface is not None
    assert surface["verified"] is True
    assert surface["derivation"] == DERIVATION_SECTION
    assert "flag" not in surface
    assert handoff_surface_action_hints("transcript:t1", surface) == []


def test_unverified_when_source_file_null() -> None:
    surface = build_handoff_surface(
        {
            "handoff_prompt": "Bled-through operator line.",
            "handoff_provenance": {
                "derivation": DERIVATION_DETACHED_STRING,
                "source_file": None,
                "source_file_sha256": None,
            },
        }
    )
    assert surface is not None
    assert surface["verified"] is False
    assert surface["flag"] == "unverified"
    assert "source_file:null" in surface["reason"] or "null" in surface["reason"]


def test_unverified_legacy_without_provenance() -> None:
    surface = build_handoff_surface({"handoff_prompt": "Legacy detached."})
    assert surface is not None
    assert surface["verified"] is False
    assert surface["derivation"] == DERIVATION_DETACHED_STRING
    assert surface["flag"] == "unverified"


def test_invalid_marker_derivation_flag() -> None:
    surface = build_handoff_surface(
        {
            "handoff_prompt": "should not happen often",
            "handoff_provenance": {"derivation": DERIVATION_SECTION_AMBIGUOUS},
        }
    )
    assert surface is not None
    assert surface["flag"] == "invalid"


def test_no_surface_without_prompt() -> None:
    assert build_handoff_surface({"handoff_provenance": {"source_file": None}}) is None
    assert build_handoff_surface({}) is None


def test_apply_read_projection_enriches_attributes_and_hints() -> None:
    row = {
        "id": "transcript:cursor-2026-06-03-0100",
        "attributes": {
            "handoff_prompt": "Detached upsert.",
            "handoff_provenance": {
                "derivation": DERIVATION_DETACHED_STRING,
                "source_file": None,
            },
        },
    }
    projected, hints = apply_handoff_read_projection(row)
    assert "handoff_surface" in projected["attributes"]
    assert projected["attributes"]["handoff_surface"]["verified"] is False
    assert hints is not None
    assert hints[0].category == "handoff_unverified"
