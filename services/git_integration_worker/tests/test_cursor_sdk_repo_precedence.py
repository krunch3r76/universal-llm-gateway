"""L3/L4/L5 closeout precedence and ambient routing tests (6341 arc step 9)."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from implement_admission.closeout_models import (
    EffectEntry,
    EffectsManifest,
    SurfaceSection,
)

from services.git_integration_worker.cursor_home import dispatch_git_identity
from services.git_integration_worker.cursor_sdk_capture_policy import (
    DeviationDisposition,
    disposition_for_deviation,
)
from services.git_integration_worker.cursor_sdk_closeout import (
    SdkRunOutcome,
    capture_wt_baseline_with_hashes,
    prepare_closeout_delivery,
)
from services.git_integration_worker.cursor_sdk_repo_precedence import (
    resolve_repo_change_set,
)

pytestmark = pytest.mark.offline


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "test@example.com"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.name", "test"],
        check=True,
        capture_output=True,
    )


def _write(path: Path, rel: str, content: str = "# v1\n") -> None:
    target = path / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def _commit_all(repo: Path, rels: tuple[str, ...]) -> None:
    for rel in rels:
        _write(repo, rel)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "baseline"],
        check=True,
        capture_output=True,
    )


def _manifest_with_write_ops(
    repo: Path,
    paths: tuple[str, ...],
) -> EffectsManifest:
    entries = [
        EffectEntry(op="write", target=str(repo / path), identity=path) for path in paths
    ]
    return EffectsManifest(
        dispatch_id="d1",
        thread_id="t1",
        capture_sources=["conversation"],
        surfaces={
            "repo": SurfaceSection(
                surface="repo",
                source="conversation",
                entries=entries,
            )
        },
        coverage={"repo": "complete"},
    )


def _manifest_with_shell(repo: Path) -> EffectsManifest:
    return EffectsManifest(
        dispatch_id="d-shell",
        thread_id="t-shell",
        capture_sources=["conversation"],
        surfaces={
            "repo": SurfaceSection(
                surface="repo",
                source="conversation",
                entries=[
                    EffectEntry(
                        op="shell",
                        target="pytest -q",
                        identity="pytest -q",
                    )
                ],
            )
        },
        coverage={"repo": "partial"},
    )


def _outcome(manifest: EffectsManifest | None = None) -> SdkRunOutcome:
    return SdkRunOutcome(
        body="done",
        status="finished",
        duration_ms=100,
        tool_call_count=1,
        effects_manifest=manifest,
        capture_branch="B",
    )


def _commit_as_dispatch(repo: Path, dispatch_id: str, message: str = "lane") -> None:
    name, email = dispatch_git_identity(dispatch_id)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "commit",
            "-m",
            message,
            f"--author={name} <{email}>",
        ],
        check=True,
        capture_output=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": name,
            "GIT_AUTHOR_EMAIL": email,
            "GIT_COMMITTER_NAME": name,
            "GIT_COMMITTER_EMAIL": email,
        },
    )


def test_l4_negative_concurrent_commit_routes_ambient_not_deleted(
    tmp_path: Path,
) -> None:
    _init_git_repo(tmp_path)
    _commit_all(tmp_path, ("README.md",))
    _write(tmp_path, "f.py", "x\n")
    baseline = capture_wt_baseline_with_hashes(tmp_path)
    assert baseline is not None
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", "f.py"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-m", "parallel land"],
        check=True,
        capture_output=True,
    )
    delivery = prepare_closeout_delivery(
        source_repo=tmp_path,
        dispatch_id="d-ambient-commit",
        outcome=_outcome(None),
        degraded_reason=None,
        thread_id="t-ambient-commit",
        work_item_ref="todo:6341-ambient-commit",
        baseline=baseline,
    )
    payload = json.loads(delivery.body)
    assert payload["files_deleted"] == []
    ambient = payload.get("files_ambient_repo_movement") or []
    assert any(
        entry["path"] == "f.py" and entry["cause"] == "ambient:concurrent_commit"
        for entry in ambient
    )


def test_own_lane_commit_attributes_files_created_not_ambient(
    tmp_path: Path,
) -> None:
    """Lane commit by dispatch identity → files_created, not ambient concurrent."""
    dispatch_id = "d-own-lane"
    _init_git_repo(tmp_path)
    _commit_all(tmp_path, ("README.md",))
    rel = "proof/own_lane.py"
    _write(tmp_path, rel, "# lane\n")
    baseline = capture_wt_baseline_with_hashes(tmp_path)
    assert baseline is not None
    assert baseline.get("admit_head")
    _commit_as_dispatch(tmp_path, dispatch_id)
    delivery = prepare_closeout_delivery(
        source_repo=tmp_path,
        dispatch_id=dispatch_id,
        outcome=_outcome(None),
        degraded_reason=None,
        thread_id="t-own-lane",
        work_item_ref="todo:own-lane-commit",
        baseline=baseline,
    )
    payload = json.loads(delivery.body)
    assert rel in payload["files_created"]
    ambient = payload.get("files_ambient_repo_movement") or []
    assert not any(entry["path"] == rel for entry in ambient)
    git_refs = (payload.get("evidence_uris") or {}).get("git_refs") or []
    assert git_refs


def test_peer_commit_after_lane_still_ambient_for_shared_path(
    tmp_path: Path,
) -> None:
    """Peer commit touching same path keeps ambient — negative control."""
    dispatch_id = "d-peer-mix"
    _init_git_repo(tmp_path)
    _commit_all(tmp_path, ("README.md",))
    rel = "shared.py"
    _write(tmp_path, rel, "# v1\n")
    baseline = capture_wt_baseline_with_hashes(tmp_path)
    assert baseline is not None
    _commit_as_dispatch(tmp_path, dispatch_id, message="lane first")
    _write(tmp_path, rel, "# v2 peer\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", rel], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-m", "peer overwrite"],
        check=True,
        capture_output=True,
    )
    delivery = prepare_closeout_delivery(
        source_repo=tmp_path,
        dispatch_id=dispatch_id,
        outcome=_outcome(None),
        degraded_reason=None,
        thread_id="t-peer-mix",
        work_item_ref="todo:peer-mix",
        baseline=baseline,
    )
    payload = json.loads(delivery.body)
    ambient = payload.get("files_ambient_repo_movement") or []
    assert rel not in payload.get("files_created", [])
    assert rel not in payload.get("files_modified", [])
    assert any(
        entry["path"] == rel and entry["cause"] == "ambient:concurrent_commit"
        for entry in ambient
    )


def test_l4_positive_scoped_lift_shell_op(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    rel = "services/scoped.py"
    _commit_all(tmp_path, (rel,))
    baseline = capture_wt_baseline_with_hashes(tmp_path)
    assert baseline is not None
    _write(tmp_path, rel, "# scoped edit\n")
    manifest = _manifest_with_shell(tmp_path)
    manifest = manifest.model_copy(
        update={
            "surfaces": {
                **manifest.surfaces,
                "repo": SurfaceSection(
                    surface="repo",
                    source="conversation",
                    entries=[
                        *manifest.surfaces["repo"].entries,
                        EffectEntry(
                            op="write",
                            target=str(tmp_path / rel),
                            identity=rel,
                        ),
                    ],
                ),
            }
        }
    )
    delivery = prepare_closeout_delivery(
        source_repo=tmp_path,
        dispatch_id="d-scoped",
        outcome=_outcome(manifest),
        degraded_reason=None,
        thread_id="t-scoped",
        work_item_ref="todo:6341-scoped",
        baseline=baseline,
        packet_text=f"files_expected:\n- {rel}\n",
    )
    payload = json.loads(delivery.body)
    assert rel in payload["files_modified"] or rel in payload["files_created"]


def test_l3_manifest_first_without_git_porcelain(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    baseline = capture_wt_baseline_with_hashes(tmp_path)
    assert baseline is not None
    rel = "brand_new.py"
    _write(tmp_path, rel, "fresh\n")
    manifest = _manifest_with_write_ops(tmp_path, (rel,))
    from services.git_integration_worker.cursor_sdk_capture_status import ChangeSet

    change_set, _extra, _div, ambient = resolve_repo_change_set(
        manifest=manifest,
        git_change_set=ChangeSet(created=(), modified=(), deleted=()),
        source_repo=tmp_path,
        baseline=baseline,
    )
    assert rel in change_set.created
    assert not any(entry.path == rel for entry in ambient)


def test_l5_ambient_token_is_census_only(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    _write(tmp_path, "f.py", "x\n")
    baseline = capture_wt_baseline_with_hashes(tmp_path)
    assert baseline is not None
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", "f.py"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-m", "parallel land"],
        check=True,
        capture_output=True,
    )
    delivery = prepare_closeout_delivery(
        source_repo=tmp_path,
        dispatch_id="d-census",
        outcome=_outcome(None),
        degraded_reason=None,
        thread_id="t-census",
        work_item_ref="todo:6341-census",
        baseline=baseline,
    )
    payload = json.loads(delivery.body)
    ambient_tokens = [
        d
        for d in payload["deviations"]
        if str(d).startswith("divergence:repo_diff_paths_unattributed:ambient:")
    ]
    assert ambient_tokens
    assert (
        disposition_for_deviation(ambient_tokens[0])
        == DeviationDisposition.CENSUS_ONLY
    )
    assert payload.get("capture_status") != "partial" or payload["status"] != "partial"
