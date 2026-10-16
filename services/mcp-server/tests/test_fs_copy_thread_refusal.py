"""Cross-sandbox copy/move with thread= must fail closed (assertion:37974 C4)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fs_impl import fs_impl
from tools.filesystem import _cross_sandbox as cross_sandbox
from tools.filesystem import _ops_paths as ops_paths
from tools.filesystem import _ops_text as ops_text
from tools.filesystem import _paths as paths_mod
from tools.filesystem._files_dispatcher import dispatch_files_op

from tools import _file_helpers

pytestmark = pytest.mark.offline

_THREAD_REFUSAL = "thread= is not supported"


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
    }
    base.update(overrides)
    return base


@pytest.fixture
def copy_roots(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Path, Path]:
    cortex_root = tmp_path / "files"
    workspaces_root = tmp_path / "project"
    cortex_root.mkdir()
    (cortex_root / "notes").mkdir()
    workspaces_root.mkdir()
    repo = workspaces_root / "universal-llm-gateway"
    (repo / ".git").mkdir(parents=True)

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
    return cortex_root, workspaces_root


def test_copy_cortex_to_workspaces_refuses_with_thread(
    copy_roots: tuple[Path, Path],
) -> None:
    cortex_root, workspaces_root = copy_roots
    src_rel = "notes/system/threads/a37974-c4/src.md"
    src = cortex_root / src_rel
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text("must-not-copy\n", encoding="utf-8")
    dest_rel = "docs/a37974-c4-dest.md"
    dest_uri = f"workspaces://universal-llm-gateway/{dest_rel}"

    result = fs_impl(
        **_fs_kwargs(
            path=f"cortex://{src_rel}",
            target=dest_uri,
            target_sandbox="",
            thread="12345",
        )
    )

    assert "error" in result, result
    assert _THREAD_REFUSAL in result["error"]
    assert not (workspaces_root / "universal-llm-gateway" / dest_rel).exists()


def test_copy_workspaces_to_cortex_refuses_with_thread(
    copy_roots: tuple[Path, Path],
) -> None:
    cortex_root, workspaces_root = copy_roots
    src_rel = "docs/a37974-c4-src.md"
    src = workspaces_root / "universal-llm-gateway" / src_rel
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text("must-not-copy\n", encoding="utf-8")
    dest_rel = "notes/system/threads/a37974-c4/dest.json"
    dest_uri = f"cortex://{dest_rel}"

    result = fs_impl(
        **_fs_kwargs(
            path=f"workspaces://universal-llm-gateway/{src_rel}",
            target=dest_uri,
            target_sandbox="",
            thread="12345",
        )
    )

    assert "error" in result, result
    assert _THREAD_REFUSAL in result["error"]
    assert not (cortex_root / dest_rel).exists()


def test_copy_cortex_to_cortex_with_thread_not_refused_by_lane_rule(
    copy_roots: tuple[Path, Path],
) -> None:
    cortex_root, _workspaces_root = copy_roots
    src_rel = "notes/system/threads/a37974-c4/same-src.md"
    dest_rel = "notes/system/threads/a37974-c4/same-dest.md"
    src = cortex_root / src_rel
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text("same-sandbox\n", encoding="utf-8")

    result = fs_impl(
        **_fs_kwargs(
            path=f"cortex://{src_rel}",
            target=f"cortex://{dest_rel}",
            target_sandbox="",
            thread="12345",
        )
    )

    if "error" in result:
        assert _THREAD_REFUSAL not in result["error"], result
    else:
        assert (cortex_root / dest_rel).is_file()
