"""Deploy stamp planning and resolve_code_version round-trip."""

from __future__ import annotations

from unittest.mock import patch

from deploy_identity.code_version import (
    reset_code_version_cache_for_tests,
    resolve_code_version,
)
from deploy_identity.source_sync_stamp import (
    format_source_sync_stamp,
    plan_source_sync_stamp,
)


def test_stamp_format_round_trips_resolve_code_version(tmp_path, monkeypatch):
    """Line 2 of the deploy stamp is what host Stargate reads at process start."""
    reset_code_version_cache_for_tests()
    monkeypatch.delenv("ULG_CODE_VERSION", raising=False)
    sha = "ab" * 20
    stamp = tmp_path / ".source_sync_stamp"
    stamp.write_text(format_source_sync_stamp(sha, synced_at="2026-10-09T18:00:00Z"), encoding="utf-8")
    monkeypatch.setenv("ULG_DEPLOY_STAMP_PATH", str(stamp))
    with patch(
        "deploy_identity.code_version.get_workspace_root",
        side_effect=RuntimeError("legacy git checkout must not win"),
    ):
        assert resolve_code_version() == sha


def test_plan_source_sync_stamp_skips_dirty_tree(tmp_path):
    """Dirty porcelain refuses a stamp so rsync bytes are not labeled by HEAD alone."""
    with patch(
        "deploy_identity.source_sync_stamp.resolve_tree_state",
        return_value="dirty",
    ):
        assert plan_source_sync_stamp(tmp_path) is None


def test_plan_source_sync_stamp_when_clean(tmp_path):
    sha = "cd" * 20
    with (
        patch(
            "deploy_identity.source_sync_stamp.resolve_tree_state",
            return_value="clean",
        ),
        patch(
            "deploy_identity.source_sync_stamp.read_checkout_head",
            return_value=sha,
        ),
    ):
        plan = plan_source_sync_stamp(tmp_path)
    assert plan is not None
    assert plan.checkout_head == sha
    assert plan.content.splitlines()[1] == sha
