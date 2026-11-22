"""Lane worktree → cortex copy witnesses and refusal matrix (todo:brg-giw-byte-safe-copy)."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fs_impl import fs_impl
from project_ops import workspaces_impl_registry
from tools._hashing import sha256_hex_of_file
from tools.filesystem import _cross_sandbox as cross_sandbox
from tools.filesystem import _ops_paths as ops_paths
from tools.filesystem import _ops_text as ops_text
from tools.filesystem import _paths as paths_mod
from tools.filesystem._files_dispatcher import dispatch_files_op

from tools import _file_helpers

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


def bytes_bin_fixture() -> bytes:
    """~70KiB crossing 64KiB chunk: all byte values, edge cases, no trailing NL."""
    parts: list[bytes] = []
    for _ in range(275):
        parts.append(bytes(range(256)))
    parts.append(b"\x00\x00\x00")
    parts.append(b"a\r\nb\rc\r\nd")
    parts.append(b"mid\xef\xbb\xbftext")
    parts.append(b"\xc3\x28\xff\xfe")
    data = b"".join(parts)
    assert len(data) > 65536
    assert not data.endswith(b"\n")
    return data


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


def _lane_relay(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
    return {
        "thread_id": LANE_THREAD,
        "current_branch": f"cursor-sdk/lane-{LANE_THREAD}",
        "association_id": 1,
        "state": "associated",
    }


def test_copy_cortex_to_workspaces_refuses_with_thread(
    copy_roots: tuple[Path, Path, Path],
) -> None:
    cortex_root, workspaces_root, _lane = copy_roots
    src_rel = "notes/system/threads/lane-copy/src.md"
    src = cortex_root / src_rel
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text("must-not-copy\n", encoding="utf-8")
    dest_rel = "docs/lane-copy-dest.md"
    dest_uri = f"workspaces://universal-llm-gateway/{dest_rel}"

    result = fs_impl(
        **_fs_kwargs(
            path=f"cortex://{src_rel}",
            target=dest_uri,
            target_sandbox="",
            thread=LANE_THREAD,
        )
    )

    assert "error" in result, result
    assert "cortex" in result["error"] and "workspaces" in result["error"]
    assert not (workspaces_root / "universal-llm-gateway" / dest_rel).exists()


def test_move_workspaces_to_cortex_refuses_with_thread_and_preserves_source(
    copy_roots: tuple[Path, Path, Path],
) -> None:
    cortex_root, _workspaces_root, lane = copy_roots
    src_rel = "docs/lane-move-src.md"
    lane_src = lane / src_rel
    lane_src.parent.mkdir(parents=True, exist_ok=True)
    payload = "must-not-move\n"
    lane_src.write_text(payload, encoding="utf-8")
    dest_rel = "notes/tmp/lane-move-dest.json"
    dest_uri = f"cortex://{dest_rel}"

    with patch("lane_branch_root.relay", side_effect=_lane_relay):
        result = fs_impl(
            **_fs_kwargs(
                op="move",
                sandbox="workspaces",
                path=src_rel,
                target=dest_uri,
                target_sandbox="",
                thread=LANE_THREAD,
            )
        )

    assert "error" in result, result
    assert "move" in result["error"]
    assert lane_src.is_file()
    assert lane_src.read_text(encoding="utf-8") == payload
    assert not (cortex_root / dest_rel).exists()


def test_lane_worktree_to_cortex_copy_bytes(
    copy_roots: tuple[Path, Path, Path],
) -> None:
    cortex_root, workspaces_root, lane = copy_roots
    src_rel = "tmp/giw-copy-proof/bytes.bin"
    payload = bytes_bin_fixture()
    lane_src = lane / src_rel
    lane_src.parent.mkdir(parents=True, exist_ok=True)
    lane_src.write_bytes(payload)
    shared_src = workspaces_root / "universal-llm-gateway" / src_rel
    shared_src.parent.mkdir(parents=True, exist_ok=True)
    shared_src.write_bytes(b"shared-checkout-wrong\n")
    dest_rel = "notes/tmp/lane-copy-proof/bytes.bin"
    dest_uri = f"cortex://{dest_rel}"

    with patch("lane_branch_root.relay", side_effect=_lane_relay):
        result = fs_impl(
            **_fs_kwargs(
                sandbox="workspaces",
                path=src_rel,
                target=dest_uri,
                target_sandbox="",
                thread=LANE_THREAD,
                if_absent=True,
            )
        )

    assert "error" not in result, result
    assert result["status"] == "copied"
    assert result.get("lane_thread") == LANE_THREAD
    assert result.get("lane_branch") == f"cursor-sdk/lane-{LANE_THREAD}"
    dest = cortex_root / dest_rel
    assert dest.read_bytes() == payload
    assert result["read_sha256"] == sha256_hex_of_file(dest)
    assert result["read_sha256"] == hashlib.sha256(payload).hexdigest()
    assert result.get("bytes") == len(payload)
    raw = str(result)
    assert payload.decode("latin-1") not in raw


def test_lane_copy_wrong_expected_read_sha256_refuses_before_dest(
    copy_roots: tuple[Path, Path, Path],
) -> None:
    cortex_root, _ws, lane = copy_roots
    src_rel = "tmp/pin-test.bin"
    lane_src = lane / src_rel
    lane_src.parent.mkdir(parents=True, exist_ok=True)
    lane_src.write_bytes(b"pin-me\n")
    dest_rel = "notes/tmp/lane-copy-proof/pin-test.bin"
    live = sha256_hex_of_file(lane_src)

    with patch("lane_branch_root.relay", side_effect=_lane_relay):
        result = fs_impl(
            **_fs_kwargs(
                sandbox="workspaces",
                path=src_rel,
                target=f"cortex://{dest_rel}",
                thread=LANE_THREAD,
                if_absent=True,
                expected_read_sha256="0" * 64,
            )
        )

    assert result.get("reason") == "source_sha256.mismatch", result
    assert result.get("actual_sha256")
    assert not (cortex_root / dest_rel).exists()


def test_lane_copy_if_absent_on_existing_dest(
    copy_roots: tuple[Path, Path, Path],
) -> None:
    cortex_root, _ws, lane = copy_roots
    src_rel = "tmp/if-absent-src.bin"
    (lane / src_rel).parent.mkdir(parents=True, exist_ok=True)
    (lane / src_rel).write_bytes(b"new\n")
    dest_rel = "notes/tmp/lane-copy-proof/existing.bin"
    dest = cortex_root / dest_rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    prior = b"prior-bytes\n"
    dest.write_bytes(prior)
    prior_sha = sha256_hex_of_file(dest)

    with patch("lane_branch_root.relay", side_effect=_lane_relay):
        result = fs_impl(
            **_fs_kwargs(
                sandbox="workspaces",
                path=src_rel,
                target=f"cortex://{dest_rel}",
                thread=LANE_THREAD,
                if_absent=True,
            )
        )

    assert result.get("reason") == "file_exists", result
    assert dest.read_bytes() == prior
    assert sha256_hex_of_file(dest) == prior_sha
    assert not any(dest.parent.glob("*.tmp-*"))


def test_lane_copy_unassociated_thread_refuses(
    copy_roots: tuple[Path, Path, Path],
) -> None:
    cortex_root, _ws, lane = copy_roots
    src_rel = "tmp/unassoc.bin"
    (lane / src_rel).parent.mkdir(parents=True, exist_ok=True)
    (lane / src_rel).write_bytes(b"x")
    dest_rel = "notes/tmp/lane-copy-proof/unassoc.bin"

    with patch(
        "lane_branch_root.relay",
        return_value={
            "thread_id": "99999",
            "current_branch": "cursor-sdk/lane-99999",
            "association_id": None,
            "state": "unassociated",
        },
    ):
        result = fs_impl(
            **_fs_kwargs(
                sandbox="workspaces",
                path=src_rel,
                target=f"cortex://{dest_rel}",
                thread="99999",
            )
        )

    assert "error" in result, result
    assert not (cortex_root / dest_rel).exists()


def test_lane_copy_verify_fail_leaves_dest_intact(
    copy_roots: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cortex_root, _ws, lane = copy_roots
    src_rel = "tmp/verify-fail-src.bin"
    (lane / src_rel).parent.mkdir(parents=True, exist_ok=True)
    (lane / src_rel).write_bytes(b"source\n")
    dest_rel = "notes/tmp/lane-copy-proof/verify-fail.bin"
    dest = cortex_root / dest_rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    prior = b"keep-me\n"
    dest.write_bytes(prior)
    prior_sha = sha256_hex_of_file(dest)

    import durable_io.atomic as atomic_mod

    def _boom(_temp: Path, _dest: Path) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(atomic_mod, "finalize_atomic_replace", _boom)
    with patch("lane_branch_root.relay", side_effect=_lane_relay):
        result = fs_impl(
            **_fs_kwargs(
                sandbox="workspaces",
                path=src_rel,
                target=f"cortex://{dest_rel}",
                thread=LANE_THREAD,
            )
        )

    assert "error" in result, result
    assert "status" not in result
    assert dest.read_bytes() == prior
    assert sha256_hex_of_file(dest) == prior_sha