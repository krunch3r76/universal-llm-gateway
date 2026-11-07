"""Unit tests for fs write responses returning written_sha256."""

from __future__ import annotations  # noqa: I001

import hashlib
from pathlib import Path
from unittest.mock import patch

import pytest
from durable_io.atomic import PreImageMismatchError

from tools import markdown_tool
from tools.filesystem import _ops_text as ops_text
from tools.filesystem import _ops_write as ops_write
from tools.filesystem import _paths as paths
from markdown_sections import replace_section as md_replace_section

pytestmark = pytest.mark.offline


@pytest.fixture
def sandbox_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    root = tmp_path / "files"
    root.mkdir()
    monkeypatch.setattr(paths, "SANDBOX_ROOT", root)
    return root


def _expected_hex(file_path: Path) -> str:
    return hashlib.sha256(file_path.read_bytes()).hexdigest()


def test_write_returns_written_sha256(
    sandbox_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ops_text, "record", lambda *_args, **_kwargs: None)
    rel = "notes/spec.md"
    content = "dense implement spec body"
    result = ops_text.write_file_impl(rel, content)

    assert result["status"] == "written"
    written_sha256 = result["written_sha256"]
    assert isinstance(written_sha256, str)
    assert written_sha256 == written_sha256.lower()
    assert ":" not in written_sha256
    assert written_sha256 == _expected_hex(sandbox_root / rel)


def test_replace_updates_written_sha256(
    sandbox_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ops_text, "record", lambda *_args, **_kwargs: None)
    rel = "notes/spec.md"
    ops_text.write_file_impl(rel, "version-one")
    before_replace = _expected_hex(sandbox_root / rel)

    result = ops_text.edit_file_impl(
        rel,
        "replace",
        "version-two",
        target="version-one",
    )

    assert result["status"] == "edited: replace"
    written_sha256 = result["written_sha256"]
    assert written_sha256 == written_sha256.lower()
    assert ":" not in written_sha256
    assert written_sha256 != before_replace
    assert written_sha256 == _expected_hex(sandbox_root / rel)


@pytest.fixture
def cortex_md_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    root = tmp_path / "files"
    root.mkdir()
    monkeypatch.setattr(paths, "SANDBOX_ROOT", root)
    monkeypatch.setattr(markdown_tool, "_FILES_ROOT", root)
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(root))
    return root


def test_md_replace_returns_written_sha256(
    cortex_md_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ops_write, "record", lambda *_args, **_kwargs: None)
    rel = "notes/spec.md"
    path = cortex_md_root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("## Body\nversion-one\n", encoding="utf-8")
    result = markdown_tool._section_patch_result(
        path,
        rel,
        "cortex",
        "Body",
        "version-one",
        "version-two",
    )
    assert result.get("status") == "replaced"
    written_sha256 = result["written_sha256"]
    assert written_sha256 == written_sha256.lower()
    assert ":" not in written_sha256
    assert written_sha256 == _expected_hex(path)


def test_md_rewrite_section_returns_written_sha256(
    cortex_md_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ops_write, "record", lambda *_args, **_kwargs: None)
    rel = "notes/spec.md"
    path = cortex_md_root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("## Body\nversion-one\n", encoding="utf-8")
    result = markdown_tool._section_write_result(
        path,
        rel,
        "cortex",
        "Body",
        "mcp.tool.markdown.section.rewritten",
        "rewritten",
        lambda t: md_replace_section(t, "Body", "version-two"),
    )
    assert result.get("status") == "rewritten"
    assert result["written_sha256"] == _expected_hex(path)


def test_md_delete_returns_written_sha256(
    cortex_md_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ops_write, "record", lambda *_args, **_kwargs: None)
    rel = "notes/spec.md"
    path = cortex_md_root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("## Body\nversion-one\n", encoding="utf-8")
    result = markdown_tool._section_delete_result(path, rel, "cortex", "Body")
    assert result.get("status") == "deleted"
    assert result["written_sha256"] == _expected_hex(path)


def test_md_replace_rmw_failure_omits_written_sha256(
    cortex_md_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ops_write, "record", lambda *_args, **_kwargs: None)
    rel = "notes/spec.md"
    path = cortex_md_root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("## Body\nversion-one\n", encoding="utf-8")
    exc = PreImageMismatchError(path, expected_sha256="a" * 64, actual_sha256="b" * 64)
    with patch(
        "tools.markdown_tool.durable_rmw_text",
        side_effect=exc,
    ):
        result = markdown_tool._section_patch_result(
            path,
            rel,
            "cortex",
            "Body",
            "version-one",
            "version-two",
        )
    assert "error" in result
    assert "written_sha256" not in result
