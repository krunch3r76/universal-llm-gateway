"""Copy dest Share URI routing + fail-closed dest hash (assertion:37974)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fs_impl import fs_impl
from tools._hashing import sha256_hex_of_file
from tools.filesystem import _cross_sandbox as cross_sandbox
from tools.filesystem import _ops_paths as ops_paths
from tools.filesystem import _ops_text as ops_text
from tools.filesystem import _paths as paths_mod
from tools.filesystem._files_dispatcher import dispatch_files_op

from tools import _file_helpers

pytestmark = pytest.mark.offline


def _fs_kwargs(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "surface": "code",
        "overflow_registry": {
            "files": dispatch_files_op,
            "copy_project_file": _workspaces_copy_must_not_run,
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
    }
    base.update(overrides)
    return base


def _workspaces_copy_must_not_run(path: str, target: str) -> dict[str, str]:
    raise AssertionError(
        f"workspaces copy_project_file must not run for cortex dest: {path!r} -> {target!r}"
    )


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


def test_copy_workspaces_to_cortex_uri_without_target_sandbox(
    copy_roots: tuple[Path, Path],
) -> None:
    """Dest cortex:// must land in cortex files, not a workspaces cortex:/ collapse."""
    cortex_root, workspaces_root = copy_roots
    src_rel = "docs/a37974-src.md"
    src = workspaces_root / "universal-llm-gateway" / src_rel
    src.parent.mkdir(parents=True)
    payload = "copy-dest-must-be-cortex-files\n"
    src.write_text(payload, encoding="utf-8")
    dest_rel = "notes/system/threads/a37974-copy/source/inventory.json"
    dest_uri = f"cortex://{dest_rel}"

    result = fs_impl(
        **_fs_kwargs(
            path=f"workspaces://universal-llm-gateway/{src_rel}",
            target=dest_uri,
            target_sandbox="",
        )
    )

    assert "error" not in result, result
    dest = cortex_root / dest_rel
    assert dest.is_file()
    assert dest.read_text(encoding="utf-8") == payload
    collapsed = workspaces_root / "cortex:" / dest_rel
    assert not collapsed.exists()
    assert result["status"] == "copied"
    assert result["to"] == dest_rel
    assert result["to_uri"] == dest_uri
    assert result["uri"] == dest_uri
    assert result["read_sha256"] == sha256_hex_of_file(dest)
    assert "cortex:/" not in result["to"]
    listed = sorted(p.name for p in dest.parent.iterdir() if p.is_file())
    assert "inventory.json" in listed

    read_back = fs_impl(**_fs_kwargs(op="read", path=result["uri"], target=""))
    assert "error" not in read_back, read_back
    assert read_back["read_sha256"] == result["read_sha256"]
    listed_fs = fs_impl(
        **_fs_kwargs(
            op="list",
            path="cortex://notes/system/threads/a37974-copy/source",
            target="",
        )
    )
    assert listed_fs.get("status") == "ok", listed_fs
    assert any(str(entry).endswith("inventory.json") for entry in listed_fs["files"])


def test_copy_refuses_when_dest_vanishes_after_shutil(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    src = tmp_path / "src.txt"
    dst = tmp_path / "dst.txt"
    src.write_text("payload", encoding="utf-8")
    real_copy = ops_paths.shutil.copy2

    def _copy_then_unlink(source: str, destination: str) -> str:
        real_copy(source, destination)
        Path(destination).unlink()
        return destination

    monkeypatch.setattr(ops_paths.shutil, "copy2", _copy_then_unlink)
    with pytest.raises(FileNotFoundError, match="not a file after copy"):
        ops_paths.copy_file_verified(src, dst)
    assert not dst.exists()


@pytest.mark.parametrize("mode", ["vanish", "mismatch"])
def test_fs_impl_copy_withholds_copied_when_verify_fails(
    copy_roots: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    """Ack layer must not emit status=copied when dest verify fails."""
    _cortex_root, workspaces_root = copy_roots
    src_rel = "docs/a37974-verify.md"
    src = workspaces_root / "universal-llm-gateway" / src_rel
    src.parent.mkdir(parents=True)
    src.write_text("source-bytes\n", encoding="utf-8")
    dest_uri = "cortex://notes/system/threads/a37974-copy/source/verify.json"
    real_copy = ops_paths.shutil.copy2

    def _tamper(source: str, destination: str) -> str:
        real_copy(source, destination)
        dest = Path(destination)
        if mode == "vanish":
            dest.unlink()
        else:
            dest.write_text("not-the-source\n", encoding="utf-8")
        return destination

    monkeypatch.setattr(ops_paths.shutil, "copy2", _tamper)
    result = fs_impl(
        **_fs_kwargs(
            path=f"workspaces://universal-llm-gateway/{src_rel}",
            target=dest_uri,
            target_sandbox="",
        )
    )
    assert "error" in result, result
    assert "status" not in result
