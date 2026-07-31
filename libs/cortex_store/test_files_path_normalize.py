"""Offline tests for shared CORTEX_FILES_ROOT path normalization."""

from __future__ import annotations

from pathlib import Path

import pytest

from cortex_store.files_path_normalize import (
    MCP_DATA_FILES_PREFIX,
    normalize_cortex_files_path,
)

pytestmark = pytest.mark.offline


def test_rewrite_doc_shorthand_to_relative(tmp_path: Path) -> None:
    root = tmp_path / "files"
    root.mkdir()
    rel, err = normalize_cortex_files_path(
        f"{MCP_DATA_FILES_PREFIX}notes/system/threads/x.md",
        root,
    )
    assert err is None
    assert rel == "notes/system/threads/x.md"


def test_relative_path_accepted(tmp_path: Path) -> None:
    root = tmp_path / "files"
    root.mkdir()
    rel, err = normalize_cortex_files_path("notes/system/threads/x.md", root)
    assert err is None
    assert rel == "notes/system/threads/x.md"


def test_cortex_uri_accepted(tmp_path: Path) -> None:
    root = tmp_path / "files"
    root.mkdir()
    rel, err = normalize_cortex_files_path(
        "cortex://notes/system/threads/x.md",
        root,
    )
    assert err is None
    assert rel == "notes/system/threads/x.md"


def test_absolute_under_live_root_accepted(tmp_path: Path) -> None:
    root = tmp_path / "files"
    root.mkdir()
    abs_path = (root / "notes/system/threads/x.md").resolve()
    rel, err = normalize_cortex_files_path(str(abs_path), root)
    assert err is None
    assert rel == "notes/system/threads/x.md"


def test_outside_root_rejects_with_live_files_root(tmp_path: Path) -> None:
    root = tmp_path / "files"
    root.mkdir()
    outside = tmp_path.parent / "outside" / "evil.md"
    outside.parent.mkdir(parents=True, exist_ok=True)
    _, err = normalize_cortex_files_path(str(outside.resolve()), root)
    assert err is not None
    assert err["reason"] == "path.outside_files_root"
    assert err["files_root"] == str(root.resolve())
    assert "/mcp-data/files/" not in err["files_root"]


def test_bare_mcp_data_prefix_rejects(tmp_path: Path) -> None:
    root = tmp_path / "files"
    root.mkdir()
    _, err = normalize_cortex_files_path("/mcp-data/notes/system/x.md", root)
    assert err is not None
    assert err["reason"] == "path.outside_files_root"
    assert "anchored prefix" in err["hint"]
