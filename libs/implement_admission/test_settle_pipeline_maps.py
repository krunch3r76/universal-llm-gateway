"""Tests for functional settle pipeline map loader."""

from __future__ import annotations

import subprocess
from pathlib import Path

from universal_workspace import get_workspace_root

from implement_admission.settle_gate import judge_settle
from implement_admission.settle_pipeline_maps import functional_settle_pipeline_maps


def test_functional_settle_pipeline_maps_includes_known_pipeline() -> None:
    repo = get_workspace_root()
    maps = functional_settle_pipeline_maps(repo)
    assert maps.pipeline_sources
    assert any(
        path.endswith(".yaml") for paths in maps.pipeline_sources.values() for path in paths
    )


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def test_broken_yaml_is_fail_attributable_via_parent_commit_map(tmp_path: Path) -> None:
    """Unparseable post-land YAML stays attributable through merge_sha^1."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "settle@example.com")
    _git(repo, "config", "user.name", "settle")
    yaml_rel = "pipelines/demo/broken.yaml"
    yaml_path = repo / yaml_rel
    yaml_path.parent.mkdir(parents=True)
    yaml_path.write_text(
        "pipeline:\n  id: broken-pipe\n  steps:\n    - type: echo\n",
        encoding="utf-8",
    )
    _git(repo, "add", yaml_rel)
    _git(repo, "commit", "-m", "parent")
    yaml_path.write_text("pipeline: [\n", encoding="utf-8")
    _git(repo, "add", yaml_rel)
    _git(repo, "commit", "-m", "break yaml")
    merge_sha = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    maps = functional_settle_pipeline_maps(repo, merge_sha=merge_sha)
    assert "broken-pipe" in maps.pipeline_sources
    assert yaml_rel in maps.pipeline_sources["broken-pipe"]
    verdict = judge_settle(
        snapshot_gateway_ids=["g1"],
        snapshot_pipeline_ids=["broken-pipe"],
        post_gateway_ids=["g1"],
        post_pipeline_ids=[],
        membership_seq=4,
        latest_catalog_seq=3,
        timed_out=False,
        land_paths=[yaml_rel],
        pipeline_sources=maps.pipeline_sources,
        step_type_modules=maps.step_type_modules,
        pipeline_step_types=maps.pipeline_step_types,
    )
    assert verdict == "fail_attributable"
