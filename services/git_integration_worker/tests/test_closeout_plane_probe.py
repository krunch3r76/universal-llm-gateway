"""Three-plane closeout probe — stranded / FF / unknown / annotate / relay."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from services.git_integration_worker.cursor_auto.closeout_plane_probe import (
    annotate_plane_discrepancy,
    inject_plane_line,
    parse_capture_plane_keys,
    preserve_plane_lines,
    probe_three_planes,
    qualify_checkpoint_value,
    qualify_deployment_state,
    render_plane_headline,
    strip_plane_line,
)
from services.git_integration_worker.cursor_auto.closeout_relay_common import (
    strip_projected_closeout_envelope,
)
from services.git_integration_worker.cursor_auto.closeout_tree_state import (
    compute_closeout_tree_state,
)

pytestmark = pytest.mark.offline


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "master")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "test")
    (repo / "README").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "README")
    _git(repo, "commit", "-m", "base")
    return repo


def _wrapper(*, head_sha: str | None, branch: str | None) -> str:
    payload: dict[str, object] = {
        "schema_version": 1,
        "status": "complete",
        "capture_status": "complete",
        "files_created": [],
    }
    if head_sha is not None:
        payload["head_sha"] = head_sha
    if branch is not None:
        payload["branch"] = branch
    return json.dumps(payload)


def test_parse_capture_plane_keys_from_wrapper() -> None:
    keys = parse_capture_plane_keys(
        _wrapper(head_sha="abc1234", branch="cursor-sdk/auto-3137b70eeaba")
    )
    assert keys.head_sha == "abc1234"
    assert keys.branch == "cursor-sdk/auto-3137b70eeaba"


def test_stranded_fixture_headline_grep_visible_not_landed(tmp_path: Path) -> None:
    """auto-3137b70eeaba shape — Lane-B commit not ancestor of master."""
    repo = _init_repo(tmp_path)
    branch = "cursor-sdk/auto-3137b70eeaba"
    _git(repo, "checkout", "-b", branch)
    (repo / "stranded.txt").write_text("stranded\n", encoding="utf-8")
    _git(repo, "add", "stranded.txt")
    _git(repo, "commit", "-m", "lane-b stranded")
    head = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "master")
    # master lacks the commit
    assert (
        subprocess.run(
            ["git", "-C", str(repo), "merge-base", "--is-ancestor", head, "master"],
            capture_output=True,
        ).returncode
        != 0
    )
    obs = probe_three_planes(repo, head_sha=head, branch=branch, as_of="2026-08-07T00:00:00Z")
    line = render_plane_headline(obs)
    assert "NOT landed@local-master" in line
    assert "committed@lane-B" in line
    assert branch in line
    assert "as-of 2026-08-07T00:00:00Z" in line
    # AC2: grep-visible without joining a second field
    assert "NOT landed@local-master" in line


def test_ff_landed_fixture_headline_landed_not_published(tmp_path: Path) -> None:
    """auto-d22534784ea9 shape — tip on master, origin tip absent or behind."""
    repo = _init_repo(tmp_path)
    branch = "cursor-sdk/auto-d22534784ea9"
    _git(repo, "checkout", "-b", branch)
    (repo / "landed.txt").write_text("landed\n", encoding="utf-8")
    _git(repo, "add", "landed.txt")
    _git(repo, "commit", "-m", "lane-b then ff")
    head = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "master")
    _git(repo, "merge", "--ff-only", branch)
    # no origin/master ref → unknown@origin, not a false unpublished claim... 
    # AC5 wants NOT published@origin when origin is behind. Create origin behind.
    _git(repo, "update-ref", "refs/remotes/origin/master", _git(repo, "rev-parse", "HEAD~1"))
    obs = probe_three_planes(repo, head_sha=head, branch=branch, as_of="2026-08-07T00:00:00Z")
    line = render_plane_headline(obs)
    assert "landed@local-master" in line
    assert "NOT landed@local-master" not in line
    assert "NOT published@origin" in line
    assert "published@origin" not in line.split("NOT published@origin")[0]


def test_degraded_capture_head_absent_never_upgraded(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    obs = probe_three_planes(repo, head_sha=None, branch="cursor-sdk/x")
    line = render_plane_headline(obs)
    assert line == "plane: unknown@lane-B (capture head absent)"
    assert "landed@local-master" not in line
    assert "committed@lane-B" not in line


def test_degraded_commit_absent_from_odb(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    phantom = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
    obs = probe_three_planes(repo, head_sha=phantom)
    line = render_plane_headline(obs)
    assert "unknown@lane-B" in line
    assert "commit absent from ODB" in line
    assert "landed@local-master" not in line


def test_discrepancy_annotates_when_deployment_lags_landed(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    head = _git(repo, "rev-parse", "HEAD")
    obs = probe_three_planes(repo, head_sha=head, as_of="t0")
    marker = annotate_plane_discrepancy(
        checkpoint="deferred@local-master: authored paths not yet path-explicit committed",
        deployment_state="authored-not-committed@local-master — 2 paths await path-explicit commit",
        plane=obs,
    )
    assert marker is not None
    assert marker.startswith("plane-discrepancy:")
    assert "lags landed@local-master" in marker


def test_relay_preserves_plane_line_through_envelope_strip() -> None:
    body = (
        "TYPE: CLOSEOUT\n"
        "status: complete\n"
        "\n"
        "status: complete\n"
        "checkpoint: committed@local-master abc1234 paths=1\n"
        "plane: committed@lane-B(cursor-sdk/x) · NOT landed@local-master · as-of t0\n"
        "plane-discrepancy: example\n"
    )
    stripped = strip_projected_closeout_envelope(body)
    assert preserve_plane_lines(stripped)
    assert "NOT landed@local-master" in stripped
    assert "plane-discrepancy: example" in stripped


def test_inject_plane_line_after_checkpoint() -> None:
    body = "status: complete\ncheckpoint: nothing_authored@local-master\n"
    out = inject_plane_line(body, value="plane: unknown@lane-B (capture head absent)")
    assert "checkpoint:" in out
    assert "plane: unknown@lane-B (capture head absent)" in out
    assert out.index("checkpoint:") < out.index("plane:")


def test_qualify_checkpoint_and_deployment_additive() -> None:
    assert (
        qualify_checkpoint_value("committed abc1234 paths=1")
        == "committed@local-master abc1234 paths=1"
    )
    assert qualify_checkpoint_value("deferred: reason") == "deferred@local-master: reason"
    assert (
        qualify_deployment_state("authored-not-committed — 2 paths await path-explicit commit")
        == "authored-not-committed@local-master — 2 paths await path-explicit commit"
    )


def test_compute_tree_state_stranded_end_to_end(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    branch = "cursor-sdk/auto-3137b70eeaba"
    _git(repo, "checkout", "-b", branch)
    (repo / "x.txt").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "x.txt")
    _git(repo, "commit", "-m", "stranded")
    head = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "master")
    wrapper = _wrapper(head_sha=head, branch=branch)
    with patch(
        "services.git_integration_worker.cursor_auto.closeout_tree_state."
        "compute_lane_a_checkpoint_value",
        return_value="deferred: authored paths not yet path-explicit committed",
    ), patch(
        "services.git_integration_worker.cursor_auto.closeout_tree_state."
        "authored_paths_for_dispatch",
        return_value=("x.txt",),
    ):
        state = compute_closeout_tree_state(
            source_repo=repo,
            dispatch_id="auto-3137b70eeaba",
            wrapper_text=wrapper,
        )
    assert "NOT landed@local-master" in state.plane_line
    assert state.checkpoint.startswith("deferred@local-master:")
    assert state.deployment_state is not None
    assert "@local-master" in state.deployment_state
    # no gate on complete — plane present regardless
    assert state.plane_line.startswith("plane:")


def test_compute_tree_state_missing_head_unknown(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    wrapper = _wrapper(head_sha=None, branch=None)
    with patch(
        "services.git_integration_worker.cursor_auto.closeout_tree_state."
        "compute_lane_a_checkpoint_value",
        return_value="nothing_authored",
    ):
        state = compute_closeout_tree_state(
            source_repo=repo,
            dispatch_id="d-empty",
            wrapper_text=wrapper,
        )
    assert state.plane_line == "plane: unknown@lane-B (capture head absent)"
    assert state.checkpoint == "nothing_authored@local-master"


def test_strip_plane_line_roundtrip() -> None:
    body = "status: complete\nplane: landed@local-master · as-of t0\n"
    assert "plane:" not in strip_plane_line(body)
