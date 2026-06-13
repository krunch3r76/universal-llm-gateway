"""Unit tests for fs write responses returning written_sha256."""

from __future__ import annotations  # noqa: I001

import hashlib
from pathlib import Path

import pytest

from tools.filesystem import _ops_text as ops_text
from tools.filesystem import _paths as paths

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
