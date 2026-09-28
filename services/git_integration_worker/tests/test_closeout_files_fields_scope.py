"""a:36755 — files_* scope tokens vs effects[] (specimen 13154 / auto-95e73a0b419f)."""

from __future__ import annotations

from services.git_integration_worker.cursor_sdk_closeout_seal import (
    closeout_files_attribution_declared,
    closeout_git_authorship_lists_empty,
    seal_closeout_payload,
)


def _giw_effect_paths() -> list[str]:
    return [
        f"services/git_integration_worker/module_{i}.py" for i in range(9)
    ]


def _specimen_13154_shape() -> dict:
    """Merge-land: empty files_*, populated effects[] and surface_counts (13154 t6)."""
    return {
        "schema_version": 1,
        "public_api_changed": False,
        "files_created": [],
        "files_modified": [],
        "files_deleted": [],
        "files_ambient_repo_movement": [],
        "effects": _giw_effect_paths(),
        "effects_manifest": {
            "schema_version": 1,
            "dispatch_id": "auto-95e73a0b419f",
            "thread_id": "13154",
            "surface_counts": {"repo": 11},
        },
    }


def _genuine_no_op_shape() -> dict:
    return {
        "schema_version": 1,
        "public_api_changed": False,
        "files_created": [],
        "files_modified": [],
        "files_deleted": [],
        "files_ambient_repo_movement": [],
        "effects": [],
        "effects_manifest": {
            "schema_version": 1,
            "dispatch_id": "d-noop",
            "thread_id": "t-noop",
            "surface_counts": {"repo": 0},
        },
    }


def test_seal_qualifies_files_attribution_lists() -> None:
    sealed = seal_closeout_payload(_specimen_13154_shape())
    assert closeout_files_attribution_declared(sealed)
    assert sealed["files_created_scope"] == sealed["files_modified_scope"]
    manifest_counts = sealed["effects_manifest"]["surface_counts"]
    assert manifest_counts["repo_scope"] == (
        "this closeout effects_manifest surface entry counts"
    )
    assert manifest_counts["repo_authority"] == "derived"


def test_empty_files_with_effects_is_not_read_as_genuine_no_op() -> None:
    """AC2: 13154 shape — authorship empty but dispatch action moved paths."""
    sealed = seal_closeout_payload(_specimen_13154_shape())
    assert closeout_git_authorship_lists_empty(sealed)
    assert sealed["effects"]
    assert closeout_files_attribution_declared(sealed)

    noop = seal_closeout_payload(_genuine_no_op_shape())
    assert closeout_git_authorship_lists_empty(noop)
    assert not noop["effects"]
    assert closeout_files_attribution_declared(noop)

    # Before scope tokens, empty files_* looked like a no-op when effects[] was ignored.
    assert closeout_git_authorship_lists_empty(sealed) == closeout_git_authorship_lists_empty(
        noop
    )
    assert bool(sealed["effects"]) != bool(noop["effects"])
    assert closeout_files_attribution_declared(sealed)
