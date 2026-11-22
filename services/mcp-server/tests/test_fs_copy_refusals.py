"""Dest-class and surface-lease refusals for lane workspaces→cortex copy."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fs_impl import fs_impl

from tools import _file_helpers
from tools._hashing import sha256_hex_of_file
from tools.filesystem import _cross_sandbox as cross_sandbox
from tools.filesystem import _ops_paths as ops_paths
from tools.filesystem import _ops_text as ops_text
from tools.filesystem import _paths as paths_mod
from tools.filesystem._files_dispatcher import dispatch_files_op

pytestmark = pytest.mark.offline

LANE_THREAD = "11579"


def _fs_kwargs(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "surface": "code",
        "overflow_registry": {
            "files": dispatch_files_op,
        },
        "op": "copy",
        "sandbox": "",
        "path": "",
        "paths": None,
        "content": "",
        "target": "",
        "target_sandbox": "",
        "line": 0,
        "section": "",
        "all_occurrences": False,
        "include_untracked": True,
        "binary": False,
        "max_depth": 3,
        "offset": 0,
        "limit": 0,
        "expected_sha256": "",
        "if_absent": False,
        "thread": None,
        "expected_read_sha256": "",
    }
    base.update(overrides)
    return base


def _lane_relay(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
    return {
        "thread_id": LANE_THREAD,
        "current_branch": f"cursor-sdk/lane-{LANE_THREAD}",
        "association_id": 1,
        "state": "associated",
    }


@pytest.fixture
def copy_roots(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Path, Path, Path]:
    cortex_root = tmp_path / "files"
    workspaces_root = tmp_path / "project"
    cortex_root.mkdir()
    (cortex_root / "notes").mkdir()
    workspaces_root.mkdir()
    repo = workspaces_root / "universal-llm-gateway"
    (repo / ".git").mkdir(parents=True)
    (repo / "scripts").mkdir(parents=True)
    (repo / "scripts" / "check-imports").write_text("")
    lane = (
        workspaces_root
        / "ulg-arc-worktrees"
        / "universal-llm-gateway"
        / f"lane-{LANE_THREAD}"
    )
    lane.mkdir(parents=True)

    monkeypatch.setenv("PROJECT_ROOT", str(workspaces_root))
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(cortex_root))
    monkeypatch.delenv("LIFE_PROJECT_ROOT", raising=False)
    monkeypatch.setattr(paths_mod, "SANDBOX_ROOT", cortex_root)
    monkeypatch.setattr(ops_paths, "SANDBOX_ROOT", cortex_root)
    monkeypatch.setattr(ops_text, "SANDBOX_ROOT", cortex_root)
    monkeypatch.setattr(cross_sandbox, "SANDBOX_ROOT", cortex_root)
    monkeypatch.setattr(_file_helpers, "FILES_ROOT", cortex_root)
    _orig_read = _file_helpers.read_file_result

    def _read(path: str, root: Path | None = None, **kwargs: Any) -> dict[str, Any]:
        return _orig_read(path, root if root is not None else cortex_root, **kwargs)

    monkeypatch.setattr(_file_helpers, "read_file_result", _read)
    monkeypatch.setattr(ops_text, "read_file_result", _read)
    monkeypatch.setattr(cross_sandbox, "record", lambda *_a, **_k: None)
    monkeypatch.setattr(ops_paths, "record", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "implement_admission.closeout_helpers.cortex_files_root",
        lambda: cortex_root,
    )
    monkeypatch.setattr(
        "implement_admission.scheme_resolve.cortex_files_root",
        lambda: cortex_root,
    )
    monkeypatch.setattr(
        "tools.filesystem._batch_ingress.cortex_files_root",
        lambda: cortex_root,
    )
    monkeypatch.setattr(
        "lane_branch_root.project_root_path", lambda: workspaces_root
    )
    monkeypatch.setattr(
        "lane_branch_root.worktree_dirname_for_branch",
        lambda _branch: f"lane-{LANE_THREAD}",
    )
    return cortex_root, workspaces_root, lane


def test_lane_copy_existing_consult_dest_refuses_unguarded_overwrite(
    copy_roots: tuple[Path, Path, Path],
) -> None:
    """AC5 — existing consult-class cortex dest refuses overwrite; dest unchanged."""
    cortex_root, _ws, lane = copy_roots
    src_rel = "tmp/consult-overwrite-src.bin"
    (lane / src_rel).parent.mkdir(parents=True, exist_ok=True)
    (lane / src_rel).write_bytes(b"attempted-bytes\n")
    dest_rel = "notes/system/threads/8801-consult-reply.md"
    dest = cortex_root / dest_rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    prior = b"incumbent-consult-bind\n"
    dest.write_bytes(prior)
    prior_sha = sha256_hex_of_file(dest)
    prior_mode = dest.stat().st_mode

    with patch("lane_branch_root.relay", side_effect=_lane_relay):
        result = fs_impl(
            **_fs_kwargs(
                sandbox="workspaces",
                path=src_rel,
                target=f"cortex://{dest_rel}",
                thread=LANE_THREAD,
            )
        )

    assert result.get("reason") == "consult_class.unguarded_overwrite", result
    assert dest.read_bytes() == prior
    assert sha256_hex_of_file(dest) == prior_sha
    assert dest.stat().st_mode == prior_mode
    assert not any(dest.parent.glob("*.tmp-*"))


def test_lane_copy_existing_shared_dest_without_expected_sha256_refuses(
    copy_roots: tuple[Path, Path, Path],
) -> None:
    """AC5 — shared-class dest requires expected_sha256 on overwrite."""
    cortex_root, _ws, lane = copy_roots
    src_rel = "tmp/shared-overwrite-src.bin"
    (lane / src_rel).parent.mkdir(parents=True, exist_ok=True)
    (lane / src_rel).write_bytes(b"new-shared-body\n")
    dest_rel = "notes/system/specs/lane-copy-shared-target.md"
    dest = cortex_root / dest_rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    prior = b"prior-shared-row\n"
    dest.write_bytes(prior)
    prior_sha = sha256_hex_of_file(dest)
    prior_mode = dest.stat().st_mode

    with patch("lane_branch_root.relay", side_effect=_lane_relay):
        result = fs_impl(
            **_fs_kwargs(
                sandbox="workspaces",
                path=src_rel,
                target=f"cortex://{dest_rel}",
                thread=LANE_THREAD,
            )
        )

    assert result.get("reason") == "expected_sha256.required", result
    assert dest.read_bytes() == prior
    assert sha256_hex_of_file(dest) == prior_sha
    assert dest.stat().st_mode == prior_mode
    assert not any(dest.parent.glob("*.tmp-*"))


def test_lane_copy_life_surface_without_workspaces_copy_grant_refuses(
    copy_roots: tuple[Path, Path, Path],
) -> None:
    """AC8 — /mcp/life lacks workspaces copy; refusal before lane work runs."""
    cortex_root, _ws, lane = copy_roots
    src_rel = "tmp/life-surface-no-copy-grant.bin"
    (lane / src_rel).parent.mkdir(parents=True, exist_ok=True)
    (lane / src_rel).write_bytes(b"x")
    dest_rel = "notes/tmp/lane-copy-proof/life-surface-refusal.bin"

    def _relay_must_not_run(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("lane relay must not run before surface permission gate")

    with patch("lane_branch_root.relay", side_effect=_relay_must_not_run):
        result = fs_impl(
            **_fs_kwargs(
                surface="life",
                sandbox="workspaces",
                path=src_rel,
                target=f"cortex://{dest_rel}",
                thread=LANE_THREAD,
            )
        )

    assert "error" in result, result
    assert "/mcp/life" in result["error"]
    assert "workspaces" in result["error"]
    assert not (cortex_root / dest_rel).exists()
