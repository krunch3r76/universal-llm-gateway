"""Hermetic coverage for read_multi Share URI ingress (friction a:37216)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fs_impl import fs_impl
from tools.filesystem import _ops_text
from tools.filesystem import _paths as paths_mod
from tools.filesystem._files_dispatcher import dispatch_files_op

from tools import _file_helpers

pytestmark = pytest.mark.offline


def _fs_kwargs(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "surface": "code",
        "overflow_registry": {"files": dispatch_files_op},
        "op": "read_multi",
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


@pytest.fixture
def cortex_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    root = tmp_path / "files"
    root.mkdir()
    (root / "notes").mkdir()
    monkeypatch.setattr(paths_mod, "SANDBOX_ROOT", root)
    monkeypatch.setattr(_file_helpers, "FILES_ROOT", root)
    # Default-arg capture at import time — rebind call sites to tmp root.
    _orig_batch = _file_helpers.read_files_batch

    def _batch(
        paths: list[str],
        batch_root: Path | None = None,
        *,
        binary: bool = False,
    ):
        return _orig_batch(
            paths, root=batch_root if batch_root is not None else root, binary=binary
        )

    monkeypatch.setattr(_file_helpers, "read_files_batch", _batch)
    monkeypatch.setattr(_ops_text, "read_files_batch", _batch)
    monkeypatch.setattr(
        "implement_admission.closeout_helpers.cortex_files_root",
        lambda: root,
    )
    monkeypatch.setattr(
        "tools.filesystem._batch_ingress.cortex_files_root",
        lambda: root,
    )
    return root


def test_read_multi_cortex_uri_resolves_like_single_read(cortex_root: Path) -> None:
    """Known-present cortex:// must not false-miss under read_multi."""
    rel = "notes/system/specs/a37216-present.md"
    body = "present-for-read-multi\n"
    target = cortex_root / rel
    target.parent.mkdir(parents=True)
    target.write_text(body, encoding="utf-8")
    uri = f"cortex://{rel}"

    result = fs_impl(**_fs_kwargs(paths=[uri]))

    assert "error" not in result, result
    files = result["files"]
    assert uri in files
    assert files[uri] == body
    assert "File not found" not in str(files[uri])


def test_read_multi_relative_plus_sandbox_still_works(cortex_root: Path) -> None:
    rel = "notes/system/specs/a37216-relative.md"
    body = "relative-control\n"
    target = cortex_root / rel
    target.parent.mkdir(parents=True)
    target.write_text(body, encoding="utf-8")

    result = fs_impl(**_fs_kwargs(sandbox="cortex", paths=[rel]))

    assert "error" not in result, result
    assert result["files"][rel] == body


def test_read_multi_absent_still_reports_missing(cortex_root: Path) -> None:
    missing = "notes/system/specs/__a37216-absent__.md"
    uri = f"cortex://{missing}"

    result = fs_impl(**_fs_kwargs(paths=[uri]))

    assert "error" not in result, result
    entry = result["files"][uri]
    assert isinstance(entry, dict)
    assert "File not found" in entry["error"]


def test_read_multi_mixed_sandboxes_refuse(cortex_root: Path) -> None:
    result = fs_impl(
        **_fs_kwargs(
            paths=[
                "cortex://notes/a.md",
                "workspaces://universal-llm-gateway/README.md",
            ]
        )
    )
    assert "error" in result
    assert "one sandbox" in result["error"]
