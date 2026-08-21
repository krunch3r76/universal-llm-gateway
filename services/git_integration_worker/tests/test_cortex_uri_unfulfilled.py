"""Friction 30289 — copy ``cortex:`` host-path leftovers into the share.

Locator functions exist so salvage can find the file. They are not a
status-claim grader. Arc 6655 ``complete×partial:work → plane-legend``
is unchanged.
"""

from __future__ import annotations

from services.git_integration_worker.cursor_auto.closeout_status_polarity import (
    annotate_status_claim_discrepancy,
    merge_plane_discrepancy_markers,
    merge_plane_legend_markers,
)
from services.git_integration_worker.cursor_sdk_capture_policy import (
    DegradeTarget,
    DeviationDisposition,
    degrade_target_for_deviation,
    disposition_for_deviation,
)
from services.git_integration_worker.cursor_sdk_manifest import (
    CORTEX_URI_SALVAGED_DEVIATION,
    cortex_impersonation_relpath,
    is_cortex_host_path_impersonation,
    normalize_expected_cortex_deliverable_uri,
    salvage_cortex_host_path_impersonations,
)
from services.git_integration_worker.cursor_sdk_manifest.offgit_deliverables import (
    _normalize_offgit_uri,
)
from services.git_integration_worker.cursor_sdk_packet import resolve_prompt_preamble

_HOST_PATH = (
    "/mnt/torus/projects/cortex:/notes/personal/kaywan/"
    "walgreens-shift-dashboard.html"
)
_OUTSIDE_TOKEN = "cortex:/notes/personal/kaywan/walgreens-shift-dashboard.html"


def test_host_path_component_cortex_colon_is_locator() -> None:
    assert is_cortex_host_path_impersonation(_HOST_PATH)
    assert is_cortex_host_path_impersonation(_OUTSIDE_TOKEN)
    assert not is_cortex_host_path_impersonation(
        "cortex://notes/personal/kaywan/walgreens-shift-dashboard.html"
    )
    assert not is_cortex_host_path_impersonation("cortex:notes/system/foo.md")


def test_normalize_expected_rejects_impersonation_keeps_shorthand() -> None:
    assert normalize_expected_cortex_deliverable_uri(_OUTSIDE_TOKEN) is None
    assert (
        normalize_expected_cortex_deliverable_uri("cortex:notes/system/foo.md")
        == "cortex://notes/system/foo.md"
    )
    assert (
        normalize_expected_cortex_deliverable_uri(
            "cortex://notes/personal/kaywan/walgreens-shift-dashboard.html"
        )
        == "cortex://notes/personal/kaywan/walgreens-shift-dashboard.html"
    )


def test_normalize_offgit_does_not_promote_impersonation() -> None:
    assert _normalize_offgit_uri(None, _OUTSIDE_TOKEN) == _OUTSIDE_TOKEN
    assert (
        _normalize_offgit_uri(None, "cortex:notes/foo.md") == "cortex://notes/foo.md"
    )


def test_salvage_copies_host_path_into_cortex_root(tmp_path) -> None:
    source = tmp_path / "workspace" / "cortex:" / "notes" / "personal" / "dash.html"
    source.parent.mkdir(parents=True)
    source.write_text("<html>dash</html>\n", encoding="utf-8")
    cortex_root = tmp_path / "cortex"
    salvaged, remaining = salvage_cortex_host_path_impersonations(
        [str(source)],
        cortex_root=cortex_root,
        mount_root=tmp_path / "workspace",
        write_tree=tmp_path / "workspace",
    )
    dest = cortex_root / "notes" / "personal" / "dash.html"
    assert remaining == ()
    assert salvaged == ("cortex://notes/personal/dash.html",)
    assert dest.read_text(encoding="utf-8") == "<html>dash</html>\n"
    assert cortex_impersonation_relpath(str(source)) == "notes/personal/dash.html"


def test_salvage_skips_existing_dest_and_reports_salvaged(tmp_path) -> None:
    cortex_root = tmp_path / "cortex"
    dest = cortex_root / "notes" / "foo.html"
    dest.parent.mkdir(parents=True)
    dest.write_text("already\n", encoding="utf-8")
    salvaged, remaining = salvage_cortex_host_path_impersonations(
        ["cortex:/notes/foo.html"],
        cortex_root=cortex_root,
        mount_root=tmp_path,
        write_tree=tmp_path,
    )
    assert remaining == ()
    assert salvaged == ("cortex://notes/foo.html",)
    assert dest.read_text(encoding="utf-8") == "already\n"


def test_salvage_token_is_annotate_only() -> None:
    assert disposition_for_deviation(CORTEX_URI_SALVAGED_DEVIATION) == (
        DeviationDisposition.ANNOTATE
    )
    assert degrade_target_for_deviation(CORTEX_URI_SALVAGED_DEVIATION) == (
        DegradeTarget.CAPTURE
    )


def test_complete_x_partial_work_stays_plane_legend() -> None:
    marker = annotate_status_claim_discrepancy(
        claim="complete",
        measurement="partial:work",
    )
    assert merge_plane_legend_markers(marker) is not None
    assert merge_plane_discrepancy_markers(marker) is None


def test_preamble_forbids_cortex_colon_host_path() -> None:
    text = resolve_prompt_preamble(
        handoff_contract="implement",
        prompt_preamble=None,
        inferred_contract=None,
    )
    assert "directory name is `cortex:`" in text
    assert "status: blocked" in text
