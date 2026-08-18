"""Regression tests for friction 22940 — git-authoritative closeout manifest fidelity."""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from implement_admission.closeout_models import (
    EffectEntry,
    EffectsManifest,
    SurfaceSection,
    Verification,
)
from implement_admission.spec import CloseoutStatus

from services.git_integration_worker.cursor_sdk_capture_status import ChangeSet
from services.git_integration_worker.cursor_sdk_closeout import (
    SdkRunOutcome,
    capture_wt_baseline_with_hashes,
    prepare_closeout_delivery,
)
from services.git_integration_worker.cursor_sdk_manifest import (
    merge_repo_paths_into_manifest,
    repo_change_set_from_manifest,
    snapshot_outside_repo_paths,
)
from services.git_integration_worker.cursor_sdk_repo_precedence import (
    resolve_repo_change_set,
)

pytestmark = pytest.mark.offline

_TRACKED = (
    "services/a.py",
    "services/b.py",
    "libs/c.py",
)


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
    *,
    dispatch_id: str = "d1",
    thread_id: str = "t1",
) -> EffectsManifest:
    entries = [
        EffectEntry(op="write", target=str(repo / path), identity=path)
        for path in paths
    ]
    return EffectsManifest(
        dispatch_id=dispatch_id,
        thread_id=thread_id,
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


def _outcome(manifest: EffectsManifest | None = None) -> SdkRunOutcome:
    return SdkRunOutcome(
        body="done",
        status="finished",
        duration_ms=100,
        tool_call_count=3,
        effects_manifest=manifest,
        capture_branch="B",
    )


def test_tracked_modifications_report_in_files_modified_not_created(
    tmp_path: Path,
) -> None:
    _init_git_repo(tmp_path)
    _commit_all(tmp_path, _TRACKED)
    baseline = capture_wt_baseline_with_hashes(tmp_path)
    assert baseline is not None
    for rel in _TRACKED:
        _write(tmp_path, rel, "# edited\n")
    manifest = _manifest_with_write_ops(tmp_path, _TRACKED)
    delivery = prepare_closeout_delivery(
        source_repo=tmp_path,
        dispatch_id="d-mod",
        outcome=_outcome(manifest),
        degraded_reason=None,
        thread_id="t-mod",
        work_item_ref="todo:22940-modified",
        baseline=baseline,
    )
    payload = json.loads(delivery.body)
    assert sorted(payload["files_modified"]) == sorted(_TRACKED)
    assert payload["files_created"] == []


def test_genuinely_new_file_reports_in_files_created_only(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    _commit_all(tmp_path, ("README.md",))
    baseline = capture_wt_baseline_with_hashes(tmp_path)
    new_rel = "services/new_module.py"
    _write(tmp_path, new_rel, "x = 1\n")
    manifest = _manifest_with_write_ops(tmp_path, (new_rel,))
    delivery = prepare_closeout_delivery(
        source_repo=tmp_path,
        dispatch_id="d-new",
        outcome=_outcome(manifest),
        degraded_reason=None,
        thread_id="t-new",
        work_item_ref="todo:22940-created",
        baseline=baseline,
    )
    payload = json.loads(delivery.body)
    assert payload["files_created"] == [new_rel]
    assert payload["files_modified"] == []


def test_cursor_and_gitignored_never_in_files_buckets(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    (tmp_path / ".gitignore").write_text(".ignored/\n", encoding="utf-8")
    _commit_all(tmp_path, ("tracked.py",))
    baseline = capture_wt_baseline_with_hashes(tmp_path)
    _write(tmp_path, "tracked.py", "# touched\n")
    _write(tmp_path, ".ignored/secret.txt", "secret\n")
    cursor_skill = tmp_path / ".cursor" / "skills" / "x" / "SKILL.md"
    cursor_skill.parent.mkdir(parents=True, exist_ok=True)
    cursor_skill.write_text("# skill\n", encoding="utf-8")
    manifest = merge_repo_paths_into_manifest(
        _manifest_with_write_ops(tmp_path, ("tracked.py",)),
        [".ignored/secret.txt", ".cursor/skills/x/SKILL.md"],
        source_repo=tmp_path,
    )
    delivery = prepare_closeout_delivery(
        source_repo=tmp_path,
        dispatch_id="d-swamp",
        outcome=_outcome(manifest),
        degraded_reason=None,
        thread_id="t-swamp",
        work_item_ref="todo:22940-swamp",
        baseline=baseline,
    )
    payload = json.loads(delivery.body)
    assert payload["files_modified"] == ["tracked.py"]
    assert payload["files_created"] == []
    for bucket in ("files_created", "files_modified", "files_deleted", "files_outside_repo"):
        for path in payload.get(bucket, []):
            assert not path.startswith(".cursor/")
            assert not path.startswith(".ignored/")
    ignored = payload.get("files_untracked_or_ignored") or []
    assert any(".ignored/" in path or path.startswith(".ignored/") for path in ignored)


def test_noop_dispatch_empty_outside_repo_with_baseline(tmp_path: Path) -> None:
    mount = tmp_path
    repo = mount / "universal-llm-gateway"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@example.com"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "test"],
        check=True,
        capture_output=True,
    )
    for idx in range(950):
        rel = f"shared/outside-{idx}.txt"
        _write(mount, rel, f"#{idx}\n")
    baseline = capture_wt_baseline_with_hashes(repo)
    assert baseline is not None
    assert len(baseline["outside_repo"]) >= 900
    delivery = prepare_closeout_delivery(
        source_repo=repo,
        dispatch_id="d-noop",
        outcome=_outcome(),
        degraded_reason=None,
        thread_id="t-noop",
        work_item_ref="todo:22940-noop",
        baseline=baseline,
    )
    payload = json.loads(delivery.body)
    assert payload.get("files_outside_repo", []) == []


def test_legacy_baseline_missing_outside_repo_deviation(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    _commit_all(tmp_path, ("x.py",))
    legacy_baseline = {"codes": {}, "hashes": {}}
    _write(tmp_path, "x.py", "# edit\n")
    delivery = prepare_closeout_delivery(
        source_repo=tmp_path,
        dispatch_id="d-legacy",
        outcome=_outcome(),
        degraded_reason=None,
        thread_id="t-legacy",
        work_item_ref="todo:22940-legacy",
        baseline=legacy_baseline,
    )
    payload = json.loads(delivery.body)
    assert "capture:outside_repo_baseline_missing" in payload["deviations"]


def test_manifest_git_disagreement_emits_single_divergence(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    _commit_all(tmp_path, ("module.py",))
    baseline = capture_wt_baseline_with_hashes(tmp_path)
    _write(tmp_path, "module.py", "# edited\n")
    manifest = _manifest_with_write_ops(tmp_path, ("module.py",))
    git_cs = ChangeSet(created=(), modified=("module.py",), deleted=())
    manifest_cs, _, _ = repo_change_set_from_manifest(
        manifest, source_repo=tmp_path
    )
    assert manifest_cs is not None
    assert manifest_cs.created == ("module.py",)
    from services.git_integration_worker.cursor_sdk_closeout import capture_wt_baseline

    resolved, _, divergence, _ambient = resolve_repo_change_set(
        manifest=manifest,
        git_change_set=git_cs,
        source_repo=tmp_path,
        baseline=baseline,
        current_porcelain=capture_wt_baseline(tmp_path),
    )
    assert divergence is True
    assert resolved.modified == ("module.py",)
    assert resolved.created == ()
    delivery = prepare_closeout_delivery(
        source_repo=tmp_path,
        dispatch_id="d-div",
        outcome=_outcome(manifest),
        degraded_reason=None,
        thread_id="t-div",
        work_item_ref="todo:22940-div",
        baseline=baseline,
    )
    payload = json.loads(delivery.body)
    assert payload["deviations"].count("divergence:manifest_vs_git_labels") == 1
    assert payload["files_modified"] == ["module.py"]
    assert payload["files_created"] == []


def test_touched_test_file_i001_degrades_to_partial(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    test_rel = "tests/test_bad_imports.py"
    _commit_all(tmp_path, (test_rel,))
    baseline = capture_wt_baseline_with_hashes(tmp_path)
    _write(
        tmp_path,
        test_rel,
        "import os\nimport sys\n\ndef test_x():\n    assert True\n",
    )
    delivery = prepare_closeout_delivery(
        source_repo=tmp_path,
        dispatch_id="d-lint",
        outcome=_outcome(_manifest_with_write_ops(tmp_path, (test_rel,))),
        degraded_reason=None,
        thread_id="t-lint",
        work_item_ref="todo:22940-lint",
        baseline=baseline,
    )
    payload = json.loads(delivery.body)
    lint_entries = [v for v in payload["verification"] if "ruff check" in v["command"]]
    assert lint_entries
    assert lint_entries[0]["exit_code"] != 0
    assert payload["status"] == CloseoutStatus.PARTIAL.value


def test_ruff_unavailable_still_delivers_with_deviation(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    _commit_all(tmp_path, ("bad.py",))
    baseline = capture_wt_baseline_with_hashes(tmp_path)
    _write(tmp_path, "bad.py", "import os\nimport sys\n")
    with patch(
        "services.git_integration_worker.cursor_sdk_closeout.lint_verification.run_touched_files_lint",
        return_value=(
            Verification(command="ruff check 1 touched files", exit_code=0),
            "verification:lint_unavailable",
        ),
    ):
        delivery = prepare_closeout_delivery(
            source_repo=tmp_path,
            dispatch_id="d-noruff",
            outcome=_outcome(),
            degraded_reason=None,
            thread_id="t-noruff",
            work_item_ref="todo:22940-noruff",
            baseline=baseline,
        )
    payload = json.loads(delivery.body)
    assert "verification:lint_unavailable" in payload["deviations"]
    assert payload["status"] in {CloseoutStatus.COMPLETE.value, CloseoutStatus.PARTIAL.value}


def test_mode_only_chmod_without_label_ops_is_not_files_modified(
    tmp_path: Path,
) -> None:
    """No-label mode-only dirt is not hash-delta authorship — ambient, not files_*."""
    _init_git_repo(tmp_path)
    rel = "mode_only.py"
    _commit_all(tmp_path, (rel,))
    baseline = capture_wt_baseline_with_hashes(tmp_path)
    target = tmp_path / rel
    mode = target.stat().st_mode
    os.chmod(target, mode ^ stat.S_IXUSR)
    delivery = prepare_closeout_delivery(
        source_repo=tmp_path,
        dispatch_id="d-mode",
        outcome=_outcome(),
        degraded_reason=None,
        thread_id="t-mode",
        work_item_ref="todo:22940-mode",
        baseline=baseline,
    )
    payload = json.loads(delivery.body)
    assert rel not in payload.get("files_modified", [])
    ambient = payload.get("files_ambient_repo_movement") or []
    assert any(entry["path"] == rel for entry in ambient)


def test_manifest_only_on_disk_file_surfaces_with_divergence(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    _commit_all(tmp_path, ("stable.py",))
    baseline = capture_wt_baseline_with_hashes(tmp_path)
    ghost = "ghost.py"
    _write(tmp_path, ghost, "# ghost\n")
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", ghost],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-m", "add ghost"],
        check=True,
        capture_output=True,
    )
    baseline = capture_wt_baseline_with_hashes(tmp_path)
    manifest = EffectsManifest(
        dispatch_id="d-ghost",
        thread_id="t-ghost",
        capture_sources=["conversation"],
        surfaces={
            "repo": SurfaceSection(
                surface="repo",
                source="conversation",
                entries=[
                    EffectEntry(
                        op="edit",
                        target=str(tmp_path / ghost),
                        identity=ghost,
                    )
                ],
            )
        },
        coverage={"repo": "complete"},
    )
    delivery = prepare_closeout_delivery(
        source_repo=tmp_path,
        dispatch_id="d-ghost",
        outcome=_outcome(manifest),
        degraded_reason=None,
        thread_id="t-ghost",
        work_item_ref="todo:22940-ghost",
        baseline=baseline,
    )
    payload = json.loads(delivery.body)
    ambient = payload.get("files_ambient_repo_movement") or []
    assert ghost in payload["files_modified"] or ghost in payload.get(
        "files_untracked_or_ignored", []
    ) or any(entry["path"] == ghost for entry in ambient)
    assert "divergence:manifest_vs_git_labels" in payload["deviations"]


def test_observed_stream_paths_do_not_fabricate_created(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    _commit_all(tmp_path, ("existing.py",))
    manifest = merge_repo_paths_into_manifest(
        None,
        ["existing.py"],
        source_repo=tmp_path,
        op="observed",
    )
    manifest_cs, _, _ = repo_change_set_from_manifest(manifest, source_repo=tmp_path)
    assert manifest_cs is not None
    assert manifest_cs.created == ()
    assert manifest_cs.modified == ()


def test_snapshot_outside_repo_excludes_cursor_cache(tmp_path: Path) -> None:
    mount = tmp_path
    repo = mount / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    _write(mount, ".cursor/rules/x.mdc", "# rule\n")
    _write(mount, ".pytest_cache/v/cache/lastfailed", "[]\n")
    _write(mount, "outside.txt", "x\n")
    outside = snapshot_outside_repo_paths(mount, [repo.resolve()])
    assert "outside.txt" in outside
    assert not any(p.startswith(".cursor/") for p in outside)
    assert not any(".pytest_cache" in p for p in outside)
