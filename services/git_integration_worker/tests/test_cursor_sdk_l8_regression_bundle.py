"""L8 offline regression bundle — four end-to-end attribution scenarios (6341 §8 L8).

Consolidates the arc's L8 acceptance row from
``cortex://notes/system/threads/6341-opus-capture-effect-attribution-answer.md`` §8:

1. **Falsifier 6 / concurrent-commit** — untracked at admit, parallel ``git add && commit``;
   closeout must not list the path in ``files_deleted`` (ambient concurrent_commit).
2. **Falsifier 3 / concurrent-recreate** — tracked path deleted by dispatch, concurrent actor
   recreates before closeout; conjunct 2 fails ⇒ no ``files_deleted`` entry.
3. **RC-3 / manifest-first without porcelain** — manifest ``write`` on new path with empty git
   ``ChangeSet`` ⇒ path in ``created`` via manifest-first precedence.
4. **L6 / deleted lib propagation sever** — ``ChangeSet.deleted`` on a lib path yields zero
   ``propagation`` and zero lib-consumer ``propagation_residue``.
"""

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

from services.git_integration_worker.cursor_sdk_capture_status import ChangeSet
from services.git_integration_worker.cursor_sdk_closeout import (
    SdkRunOutcome,
    build_implement_closeout_body,
    capture_wt_baseline_with_hashes,
    prepare_closeout_delivery,
)
from services.git_integration_worker.cursor_sdk_deliverables import (
    sidecar_workspaces_ref,
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


def _manifest_with_delete_ops(
    repo: Path,
    paths: tuple[str, ...],
) -> EffectsManifest:
    entries = [
        EffectEntry(op="delete", target=str(repo / path), identity=path) for path in paths
    ]
    return EffectsManifest(
        dispatch_id="d-delete",
        thread_id="t-delete",
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
        tool_call_count=1,
        effects_manifest=manifest,
        capture_branch="B",
    )


def test_l8_falsifier6_concurrent_commit_not_files_deleted(
    tmp_path: Path,
) -> None:
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
        dispatch_id="d-l8-f6",
        outcome=_outcome(None),
        degraded_reason=None,
        thread_id="t-l8-f6",
        work_item_ref="todo:6341-l8-f6",
        baseline=baseline,
    )
    payload = json.loads(delivery.body)
    assert payload["files_deleted"] == []
    ambient = payload.get("files_ambient_repo_movement") or []
    assert any(
        entry["path"] == "f.py" and entry["cause"] == "ambient:concurrent_commit"
        for entry in ambient
    )


def test_l8_falsifier3_concurrent_recreate_suppresses_deletion(
    tmp_path: Path,
) -> None:
    victim = "victim.py"
    _init_git_repo(tmp_path)
    _commit_all(tmp_path, (victim,))
    baseline = capture_wt_baseline_with_hashes(tmp_path)
    assert baseline is not None

    (tmp_path / victim).unlink()

    _write(tmp_path, victim, "# recreated by concurrent actor\n")
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", victim],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-m", "concurrent recreate"],
        check=True,
        capture_output=True,
    )
    assert (tmp_path / victim).is_file()

    manifest = _manifest_with_delete_ops(tmp_path, (victim,))
    delivery = prepare_closeout_delivery(
        source_repo=tmp_path,
        dispatch_id="d-l8-f3",
        outcome=_outcome(manifest),
        degraded_reason=None,
        thread_id="t-l8-f3",
        work_item_ref="todo:6341-l8-f3",
        baseline=baseline,
    )
    payload = json.loads(delivery.body)
    assert victim not in payload["files_deleted"]
    assert payload["files_deleted"] == []


def test_l8_rc3_manifest_first_without_porcelain(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    baseline = capture_wt_baseline_with_hashes(tmp_path)
    assert baseline is not None
    rel = "brand_new.py"
    _write(tmp_path, rel, "fresh\n")
    manifest = _manifest_with_write_ops(tmp_path, (rel,))
    change_set, _extra, _div, ambient = resolve_repo_change_set(
        manifest=manifest,
        git_change_set=ChangeSet(created=(), modified=(), deleted=()),
        source_repo=tmp_path,
        baseline=baseline,
    )
    assert rel in change_set.created
    assert not any(entry.path == rel for entry in ambient)


def test_l8_l6_no_propagation_from_deleted_lib() -> None:
    body = build_implement_closeout_body(
        dispatch_id="l8-deleted-lib",
        outcome=_outcome(),
        degraded_reason=None,
        sidecar_ref=sidecar_workspaces_ref("l8-deleted-lib"),
        result_bytes=4,
        thread_id="t-l8-l6",
        work_item_ref="todo:6341-l8-l6",
        change_set=ChangeSet(
            created=(),
            modified=(),
            deleted=("libs/deploy_identity/__init__.py",),
        ),
    )
    payload = json.loads(body)
    assert payload["propagation_residue"] == []
    assert payload["propagation"] == []
