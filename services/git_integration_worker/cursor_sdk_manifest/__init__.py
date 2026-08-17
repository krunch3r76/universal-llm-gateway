"""First-party multi-surface effects manifest for cursor-sdk closeout."""

from __future__ import annotations

from .body_serialization import (
    compact_manifest_for_body,
    serialize_effects_manifest_for_body,
)
from .change_set_reconcile import (
    git_manifest_label_divergence,
    resolve_repo_change_set,
    verification_change_set,
    _path_is_tracked,
)
from .cortex_surface import (
    cortex_surface_has_write_op,
    harvest_cortex_assertion_ids,
    merge_stream_cortex_entries,
    _cortex_entry_from_stream_observation,
)
from .effect_entries import (
    classify_mcp_capture_branch,
    no_capture_degraded_reason,
    _entry_from_tool_call,
)
from .fs_targets import (
    manifest_fs_targets,
    manifest_fs_write_targets,
    resolve_fs_target_absolute,
)
from .manifest_build import build_effects_manifest
from .manifest_merge import (
    is_genuinely_no_code_change,
    merge_artifact_paths,
    merge_repo_paths_into_manifest,
    merge_stream_tool_calls,
    merge_wrapper_manifest,
)
from .mount_resolution import (
    classify_mount_path,
    mount_relative_path,
    registered_repo_roots,
    resolve_mount_root,
)
from .offgit_deliverables import (
    collect_expected_cortex_deliverable_uris,
    manifest_offgit_deliverable_uris,
    normalize_expected_cortex_deliverable_uri,
    oob_cortex_write_findings,
    _normalize_offgit_uri,
)
from .repo_projection import (
    manifest_repo_paths,
    manifest_repo_write_paths,
    repo_change_set_from_manifest,
    _normalize_repo_path,
)
from .surface_taxonomy import (
    CaptureBranch,
    _REPO_FILE_OPS,
    _REPO_WRITE_OPS,
)

# cursor_sdk_outside_census lazily imports registered_repo_roots back from this
# package (cursor_sdk_outside_census.py:76), so this as-self pass-through must
# stay LAST. Default isort would hoist services.* above relative imports.
# isort: off
from services.git_integration_worker.cursor_sdk_outside_census import (
    snapshot_outside_repo_paths as snapshot_outside_repo_paths,
)
# isort: on

__all__ = [
    "CaptureBranch",
    "build_effects_manifest",
    "classify_mcp_capture_branch",
    "classify_mount_path",
    "collect_expected_cortex_deliverable_uris",
    "compact_manifest_for_body",
    "cortex_surface_has_write_op",
    "git_manifest_label_divergence",
    "harvest_cortex_assertion_ids",
    "is_genuinely_no_code_change",
    "manifest_fs_targets",
    "manifest_fs_write_targets",
    "manifest_offgit_deliverable_uris",
    "manifest_repo_paths",
    "manifest_repo_write_paths",
    "merge_artifact_paths",
    "merge_repo_paths_into_manifest",
    "merge_stream_cortex_entries",
    "merge_stream_tool_calls",
    "merge_wrapper_manifest",
    "mount_relative_path",
    "no_capture_degraded_reason",
    "normalize_expected_cortex_deliverable_uri",
    "oob_cortex_write_findings",
    "registered_repo_roots",
    "repo_change_set_from_manifest",
    "resolve_fs_target_absolute",
    "resolve_mount_root",
    "resolve_repo_change_set",
    "serialize_effects_manifest_for_body",
    "snapshot_outside_repo_paths",
    "verification_change_set",
    "_REPO_FILE_OPS",
    "_REPO_WRITE_OPS",
    "_cortex_entry_from_stream_observation",
    "_entry_from_tool_call",
    "_normalize_offgit_uri",
    "_normalize_repo_path",
    "_path_is_tracked",
]
