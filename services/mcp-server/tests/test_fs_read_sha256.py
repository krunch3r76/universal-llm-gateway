"""Unit tests for fs read responses returning read_sha256."""

from __future__ import annotations  # noqa: I001

import hashlib
from pathlib import Path

import pytest

from tools import _file_helpers
from tools._file_helpers import read_file_result
from tools.filesystem import _paths as paths

pytestmark = pytest.mark.offline


@pytest.fixture
def sandbox_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    root = tmp_path / "files"
    root.mkdir()
    monkeypatch.setattr(paths, "SANDBOX_ROOT", root)
    monkeypatch.setattr(_file_helpers, "FILES_ROOT", root)
    return root


def _expected_hex(file_path: Path) -> str:
    return hashlib.sha256(file_path.read_bytes()).hexdigest()


def test_read_returns_read_sha256(
    sandbox_root: Path,
) -> None:
    rel = "notes/spec.md"
    content = "attestable read body\nline two\n"
    target = sandbox_root / rel
    target.parent.mkdir(parents=True)
    target.write_text(content, encoding="utf-8")

    result = read_file_result(rel, root=sandbox_root)

    read_sha256 = result["read_sha256"]
    assert isinstance(read_sha256, str)
    assert read_sha256 == read_sha256.lower()
    assert ":" not in read_sha256
    assert read_sha256 == _expected_hex(target)


def test_read_offset_does_not_change_read_sha256(
    sandbox_root: Path,
) -> None:
    rel = "notes/window.md"
    content = "alpha\nbeta\ngamma\n"
    target = sandbox_root / rel
    target.parent.mkdir(parents=True)
    target.write_text(content, encoding="utf-8")

    full = read_file_result(rel, root=sandbox_root)
    window = read_file_result(rel, root=sandbox_root, offset=1, limit=1)

    assert full["read_sha256"] == window["read_sha256"] == _expected_hex(target)
    assert full["content"] != window["content"]
    assert window["line_range"]["returned"] == 1


def test_read_binary_returns_read_sha256(
    sandbox_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rel = "assets/fixture.bin"
    raw = b"\x00binary\xffpayload"
    target = sandbox_root / rel
    target.parent.mkdir(parents=True)
    target.write_bytes(raw)

    result = read_file_result(rel, root=sandbox_root, binary=True)

    assert result["read_sha256"] == _expected_hex(target)
    assert "content_base64" in result
