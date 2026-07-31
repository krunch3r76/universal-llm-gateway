"""L7 read/write op split — observed must not drive write evidence or runtime surface."""

from __future__ import annotations

import hashlib
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
    repo_diff_unattributed_deviation,
)
from services.git_integration_worker.cursor_sdk_manifest import (
    is_genuinely_no_code_change,
    merge_repo_paths_into_manifest,
    repo_change_set_from_manifest,
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


def _write(path: Path, rel: str, content: str) -> None:
    target = path / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def _commit_all(repo: Path, rels: tuple[str, ...]) -> None:
    for rel in rels:
        _write(repo, rel, f"# {rel}\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "baseline"],
        check=True,
        capture_output=True,
    )


def _observed_only_manifest(paths: tuple[str, ...]) -> EffectsManifest:
    entries = [
        EffectEntry(op="observed", target=path, identity=path) for path in paths
    ]
    return EffectsManifest(
        dispatch_id="d-obs",
        thread_id="t-obs",
        capture_sources=["stream"],
        surfaces={
            "repo": SurfaceSection(
                surface="repo",
                source="stream",
                entries=entries,
            )
        },
        coverage={"repo": "complete"},
    )


def _write_manifest(path: str) -> EffectsManifest:
    return EffectsManifest(
        dispatch_id="d-write",
        thread_id="t-write",
        capture_sources=["conversation"],
        surfaces={
            "repo": SurfaceSection(
                surface="repo",
                source="conversation",
                entries=[
                    EffectEntry(op="write", target=path, identity=path),
                ],
            )
        },
        coverage={"repo": "complete"},
    )


def test_observed_only_manifest_is_genuinely_no_code_change(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    _commit_all(tmp_path, ("README.md",))
    manifest = _observed_only_manifest(("services/read_only.py",))
    empty_git = ChangeSet(created=(), modified=(), deleted=())

    assert is_genuinely_no_code_change(git_change_set=empty_git, base=manifest) is True


def test_shell_only_manifest_declares_runtime_surface() -> None:
    manifest = EffectsManifest(
        dispatch_id="d-shell",
        thread_id="t-shell",
        capture_sources=["conversation"],
        surfaces={
            "repo": SurfaceSection(
                surface="repo",
                source="conversation",
                entries=[EffectEntry(op="shell", target="pytest -q", identity="pytest -q")],
            )
        },
        coverage={"repo": "complete"},
    )
    empty_git = ChangeSet(created=(), modified=(), deleted=())

    assert is_genuinely_no_code_change(git_change_set=empty_git, base=manifest) is False


def test_write_manifest_declares_runtime_surface(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    _commit_all(tmp_path, ("README.md",))
    manifest = _write_manifest("services/new.py")
    empty_git = ChangeSet(created=(), modified=(), deleted=())

    assert is_genuinely_no_code_change(git_change_set=empty_git, base=manifest) is False


def test_observed_stream_paths_do_not_fabricate_created(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    _commit_all(tmp_path, ("README.md",))
    manifest = merge_repo_paths_into_manifest(
        None,
        ["services/stream_seen.py"],
        source_repo=tmp_path,
        source_label="stream",
        op="observed",
    )
    assert manifest is not None
    change_set, _, _ = repo_change_set_from_manifest(manifest, source_repo=tmp_path)
    assert change_set is not None
    assert change_set.created == ()
    assert change_set.modified == ()
    assert change_set.deleted == ()


def test_write_op_populates_hash_bound_evidence(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    rel = "services/evidence.py"
    _commit_all(tmp_path, (rel,))
    admit_hash = hashlib.sha256((tmp_path / rel).read_bytes()).hexdigest()
    _write(tmp_path, rel, "# changed\n")
    manifest = _write_manifest(rel)
    change_set = ChangeSet(created=(), modified=(rel,), deleted=())
    baseline = {"codes": {rel: " M"}, "hashes": {rel: admit_hash}}

    _, scoped = repo_diff_unattributed_deviation(
        change_set=change_set,
        manifest=manifest,
        source_repo=tmp_path,
        files_expected=[rel],
        baseline=baseline,
    )

    assert scoped is None


def test_observed_only_does_not_populate_write_evidence_for_hard_fail(
    tmp_path: Path,
) -> None:
    _init_git_repo(tmp_path)
    rel = "services/concurrent_touch.py"
    _commit_all(tmp_path, (rel,))
    admit_hash = hashlib.sha256((tmp_path / rel).read_bytes()).hexdigest()
    _write(tmp_path, rel, "# concurrent edit\n")
    manifest = _observed_only_manifest((rel,))
    change_set = ChangeSet(created=(), modified=(rel,), deleted=())
    baseline = {"codes": {rel: " M"}, "hashes": {rel: admit_hash}}

    _, scoped = repo_diff_unattributed_deviation(
        change_set=change_set,
        manifest=manifest,
        source_repo=tmp_path,
        files_expected=[rel],
        baseline=baseline,
    )

    assert scoped is not None
    assert "divergence:repo_diff_paths_unattributed:" in scoped
