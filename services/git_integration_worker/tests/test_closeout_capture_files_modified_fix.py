"""Regression tests for friction 21239 — manifest-scoped files_modified attribution."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from implement_admission.closeout_models import (
    EffectEntry,
    EffectsManifest,
    SurfaceSection,
)

from services.git_integration_worker.cursor_sdk_capture_status import (
    ChangeSet,
    resolve_closeout_capture_fields,
)
from services.git_integration_worker.cursor_sdk_closeout import (
    SdkRunOutcome,
    prepare_closeout_delivery,
)
from services.git_integration_worker.cursor_sdk_manifest import (
    _normalize_repo_path,
    repo_change_set_from_manifest,
)

pytestmark = pytest.mark.offline

_SPECIMEN_FILES = (
    "libs/cortex_store/ops_entities.py",
    "libs/cortex_store/skill_suggest_rank.py",
    "services/git_integration_worker/cursor_sdk_manifest.py",
    "services/git_integration_worker/cursor_sdk_closeout.py",
    "services/git_integration_worker/cursor_sdk_capture_status.py",
)

_CONCURRENT_PORCELAIN = (
    "scripts/model_manager/ui/controller/gpu_docker_preflight.py",
    "scripts/model_manager/ui/view/screens/home.py",
    "docs/tool-reference.md",
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


def _write_repo_files(repo_root: Path, rel_paths: tuple[str, ...]) -> None:
    for rel in rel_paths:
        target = repo_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# edited\n", encoding="utf-8")


def _specimen_manifest(*, dispatch_id: str, thread_id: str, repo_root: Path) -> EffectsManifest:
    """13 edit entries across 5 files + 8 shell entries; absolute SDK paths."""
    edit_counts = (3, 3, 2, 2, 3)
    edit_entries: list[EffectEntry] = []
    for path, count in zip(_SPECIMEN_FILES, edit_counts, strict=True):
        abs_path = str(repo_root / path)
        for _ in range(count):
            edit_entries.append(
                EffectEntry(op="edit", target=abs_path, identity=abs_path)
            )
    assert len(edit_entries) == 13
    shell_entries = [
        EffectEntry(op="shell", target=f"cmd-{idx}", identity=f"cmd-{idx}")
        for idx in range(8)
    ]
    return EffectsManifest(
        dispatch_id=dispatch_id,
        thread_id=thread_id,
        capture_sources=["conversation"],
        surfaces={
            "repo": SurfaceSection(
                surface="repo",
                source="conversation",
                entries=[*edit_entries, *shell_entries],
            )
        },
        coverage={"repo": "complete"},
    )


def _specimen_git_change_set() -> ChangeSet:
    modified = tuple(sorted({*_SPECIMEN_FILES, *_CONCURRENT_PORCELAIN}))
    return ChangeSet(created=(), modified=modified, deleted=())


def test_normalize_repo_path_without_repo_root_unchanged() -> None:
    assert _normalize_repo_path("  /libs/foo.py  ") == "libs/foo.py"
    assert _normalize_repo_path(None) is None
    assert _normalize_repo_path("") is None


def test_normalize_repo_path_strips_repo_root(tmp_path: Path) -> None:
    repo_root = tmp_path / "universal-llm-gateway"
    repo_root.mkdir()
    abs_path = str(repo_root / "libs/cortex_store/ops_entities.py")
    assert (
        _normalize_repo_path(abs_path, repo_root=repo_root)
        == "libs/cortex_store/ops_entities.py"
    )
    lstrip_path = abs_path.lstrip("/")
    assert (
        _normalize_repo_path(lstrip_path, repo_root=repo_root)
        == "libs/cortex_store/ops_entities.py"
    )


def test_repo_change_set_from_manifest_dedupes_and_canonicalizes(tmp_path: Path) -> None:
    manifest = _specimen_manifest(
        dispatch_id="d-spec",
        thread_id="t-spec",
        repo_root=tmp_path,
    )
    change_set, _, _ = repo_change_set_from_manifest(manifest, source_repo=tmp_path)
    assert change_set is not None
    assert change_set.modified == _SPECIMEN_FILES
    assert change_set.created == ()
    assert change_set.deleted == ()
    assert not any("mnt/torus" in path for path in change_set.modified)


def _commit_repo_files(repo_root: Path, rel_paths: tuple[str, ...]) -> None:
    for rel in rel_paths:
        target = repo_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# baseline\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo_root), "add", "."], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo_root), "commit", "-m", "baseline"],
        check=True,
        capture_output=True,
    )


def test_closeout_files_modified_excludes_concurrent_porcelain(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    manifest = _specimen_manifest(
        dispatch_id="d-closeout",
        thread_id="t-closeout",
        repo_root=tmp_path,
    )
    _commit_repo_files(tmp_path, _SPECIMEN_FILES + _CONCURRENT_PORCELAIN)
    _write_repo_files(tmp_path, _SPECIMEN_FILES)
    outcome = SdkRunOutcome(
        body="done",
        status="finished",
        duration_ms=100,
        tool_call_count=21,
        effects_manifest=manifest,
        capture_branch="B",
    )
    delivery = prepare_closeout_delivery(
        source_repo=tmp_path,
        dispatch_id="d-closeout",
        outcome=outcome,
        degraded_reason=None,
        thread_id="t-closeout",
        work_item_ref="todo:closeout-capture-files-modified-fix",
        baseline={"codes": {}, "hashes": {}, "outside_repo": []},
        deliverables_expected=True,
        packet_text=(
            "<scope>\nFiles expected:\n"
            + "\n".join(f"- `{path}`" for path in _SPECIMEN_FILES)
            + "\n</scope>\n"
        ),
    )
    payload = json.loads(delivery.body)
    assert sorted(payload["files_modified"]) == sorted(_SPECIMEN_FILES)
    assert payload["files_created"] == []
    assert payload["files_deleted"] == []
    body_text = delivery.body
    assert "mnt/torus" not in body_text
    assert "divergence:repo_diff_mismatch" not in payload["deviations"]
    assert "capture:shell_repo_writes_unverified" in payload["deviations"]
    assert payload["capture_status"] == "complete"


def test_cross_check_ignores_concurrent_git_extra(tmp_path: Path) -> None:
    manifest = _specimen_manifest(
        dispatch_id="d-xcheck",
        thread_id="t-xcheck",
        repo_root=tmp_path,
    )
    _write_repo_files(tmp_path, _SPECIMEN_FILES)
    _write_repo_files(tmp_path, _CONCURRENT_PORCELAIN)
    capture_status, divergence_reason, deviations, checked = (
        resolve_closeout_capture_fields(
            deliverables_expected=True,
            baseline={"codes": {}, "hashes": {}},
            files_expected=list(_SPECIMEN_FILES),
            degraded_reason=None,
            change_set=_specimen_git_change_set(),
            divergent_rels=(),
            source_repo=tmp_path,
            cortex_root=tmp_path / "cortex",
            manifest=manifest,
        )
    )
    assert divergence_reason is None
    assert checked is not None
    assert checked.surfaces["repo"].cross_check is None
    assert checked.coverage["repo"] == "complete"
    assert "capture:shell_repo_writes_unverified" in deviations
    assert capture_status == "complete"
