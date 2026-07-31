"""Read-side handoff surface-but-flag consumer (agent-bus thread 1188)."""

from __future__ import annotations

from cortex_store.handoff_derivation import (
    DERIVATION_AUTO_PERSISTED,
    DERIVATION_DETACHED_STRING,
    DERIVATION_SECTION,
    DERIVATION_SECTION_AMBIGUOUS,
)
from cortex_store.handoff_surface import (
    apply_handoff_read_projection,
    build_handoff_surface,
    build_handoff_surface_preview,
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


def test_surface_preview_unverified_for_detached_string() -> None:
    # Write-time mirror: an inline prompt with detached_string provenance and
    # no source file is the exact symptom the close advisory must surface.
    preview = build_handoff_surface_preview(
        "Inline detached handoff.",
        {
            "derivation": DERIVATION_DETACHED_STRING,
            "source_file": None,
            "source_file_sha256": None,
        },
    )
    assert preview is not None
    assert preview["verified"] is False
    assert preview["flag"] == "unverified"
    assert preview["derivation"] == DERIVATION_DETACHED_STRING


def test_surface_preview_none_for_verified_section() -> None:
    # Verified (file-backed marker) path carries no advisory.
    assert (
        build_handoff_surface_preview(
            "Continue from marker.",
            {
                "derivation": DERIVATION_SECTION,
                "source_file": "notes/system/sessions/h.md",
                "source_file_sha256": "sha256:abc",
            },
        )
        is None
    )


def test_surface_preview_none_without_prompt() -> None:
    # No handoff supplied → no advisory regardless of provenance.
    assert build_handoff_surface_preview(None, None) is None
    assert build_handoff_surface_preview("", None) is None


def test_auto_persisted_is_distinct_from_section() -> None:
    surface = build_handoff_surface(
        {
            "handoff_prompt": "Inline persisted.",
            "handoff_provenance": {
                "derivation": DERIVATION_AUTO_PERSISTED,
                "source_file": "notes/system/handoffs/web-2026-06-03-0100.md",
                "source_file_sha256": "sha256:abc",
            },
        }
    )
    assert surface is not None
    assert surface["derivation"] == DERIVATION_AUTO_PERSISTED
    assert surface["verified"] is False
    assert surface["flag"] == "unverified"


def test_effective_derivation_preserves_auto_persisted() -> None:
    assert (
        effective_handoff_derivation({"derivation": DERIVATION_AUTO_PERSISTED})
        == DERIVATION_AUTO_PERSISTED
    )


def test_surface_threads_handoff_verification() -> None:
    verification = {
        "checks": [{"name": "transcript_anchor_present", "status": "passed"}],
        "passed": 1,
        "total": 1,
    }
    surface = build_handoff_surface(
        {
            "handoff_prompt": "Continue.",
            "handoff_provenance": {
                "derivation": DERIVATION_AUTO_PERSISTED,
                "source_file": "notes/system/handoffs/s.md",
            },
            "handoff_verification": verification,
        }
    )
    assert surface is not None
    assert surface["handoff_verification"] == verification


def test_surface_preview_none_when_verification_all_passed() -> None:
    verification = {"checks": [], "passed": 3, "total": 3}
    assert (
        build_handoff_surface_preview(
            "Inline.",
            {
                "derivation": DERIVATION_AUTO_PERSISTED,
                "source_file": "notes/system/handoffs/s.md",
            },
            handoff_verification=verification,
        )
        is None
    )
