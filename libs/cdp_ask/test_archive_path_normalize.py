"""Offline tests for CDP ask archive_path normalization (a:25303)."""

from __future__ import annotations

from pathlib import Path

import pytest

from cdp_ask.models import SubmitProjectAskRequest
from cdp_ask.runner import (
    ArchivePathError,
    default_archive_path,
    resolve_archive_path,
)

pytestmark = pytest.mark.offline


def test_resolve_archive_path_rewrites_doc_shorthand(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "files"
    root.mkdir()
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(root))
    resolved = resolve_archive_path(
        "/mcp-data/files/notes/system/threads/x.md",
    )
    assert resolved == str((root / "notes/system/threads/x.md").resolve())


def test_resolve_archive_path_accepts_relative(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "files"
    root.mkdir()
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(root))
    resolved = resolve_archive_path("notes/system/threads/x.md")
    assert resolved == str((root / "notes/system/threads/x.md").resolve())


def test_resolve_archive_path_accepts_cortex_uri(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "files"
    root.mkdir()
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(root))
    resolved = resolve_archive_path("cortex://notes/system/threads/x.md")
    assert resolved == str((root / "notes/system/threads/x.md").resolve())


def test_resolve_archive_path_rejects_outside_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "files"
    root.mkdir()
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(root))
    outside = tmp_path.parent / "outside" / "evil.md"
    outside.parent.mkdir(parents=True, exist_ok=True)
    with pytest.raises(ArchivePathError) as exc:
        resolve_archive_path(str(outside.resolve()))
    teaching = exc.value.teaching
    assert teaching["files_root"] == str(root.resolve())
    assert teaching["reason"] == "archive_path.outside_files_root"


def test_default_archive_path_normalizes_shorthand(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "files"
    root.mkdir()
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(root))
    req = SubmitProjectAskRequest(
        converse=True,
        no_project_uuid=True,
        archive_path="/mcp-data/files/notes/system/threads/custom.md",
    )
    path = default_archive_path(req, execution_id="deadbeef" * 4)
    assert path == str((root / "notes/system/threads/custom.md").resolve())
