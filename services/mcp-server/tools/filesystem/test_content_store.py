"""Content-store retention and resolve_sha256 (item-15 / AC-15a–c)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.filesystem import _content_store as content_store
from tools.filesystem import _ops_text as ops_text
from tools.filesystem import _ops_write as ops_write
from tools.filesystem import _paths as paths


@pytest.fixture
def sandbox_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    root = tmp_path / "files"
    root.mkdir()
    monkeypatch.setattr(paths, "SANDBOX_ROOT", root)
    return root


def test_unguarded_overwrite_echoes_replaced_sha256(
    sandbox_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ops_write, "record", lambda *_args, **_kwargs: None)
    rel = "notes/spec.md"
    first = ops_text.write_file_impl(rel, "version-one")
    replaced = first["written_sha256"]
    second = ops_text.write_file_impl(rel, "version-two")
    assert second["status"] == "written"
    assert second["replaced_sha256"] == replaced
    assert second["written_sha256"] != replaced


def test_prior_content_retained_in_content_store(
    sandbox_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ops_write, "record", lambda *_args, **_kwargs: None)
    rel = "notes/spec.md"
    first = ops_text.write_file_impl(rel, "version-one")
    prior_sha = first["written_sha256"]
    ops_text.write_file_impl(rel, "version-two")
    resolved = content_store.resolve_sha256_impl(prior_sha)
    assert resolved["resolved"] is True
    assert resolved["source"] == "content_store"
    store_path = content_store.store_path_for_hex(prior_sha)
    assert store_path.read_text(encoding="utf-8") == "version-one"


def test_cas_success_also_echoes_replaced_sha256(
    sandbox_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ops_write, "record", lambda *_args, **_kwargs: None)
    rel = "notes/spec.md"
    first = ops_text.write_file_impl(rel, "before")
    current = paths.sha256_of_file(sandbox_root / rel)
    result = ops_text.write_file_impl(rel, "after", expected_sha256=current)
    assert result["status"] == "written"
    assert result["replaced_sha256"] == first["written_sha256"]


def test_resolve_sha256_stale_unknown(
    sandbox_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ops_write, "record", lambda *_args, **_kwargs: None)
    missing = "ea6d4404" + "0" * 56
    resolved = content_store.resolve_sha256_impl(missing)
    assert resolved["resolved"] is False
    assert resolved["stale"] is True
    assert resolved["source"] is None


def test_resolve_sha256_accepts_sha256_prefix(
    sandbox_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ops_write, "record", lambda *_args, **_kwargs: None)
    rel = "notes/prefixed.md"
    written = ops_text.write_file_impl(rel, "payload")
    sha = written["written_sha256"]
    ops_text.write_file_impl(rel, "clobber")
    resolved = content_store.resolve_sha256_impl(f"sha256:{sha}")
    assert resolved["resolved"] is True
    assert resolved["sha256"] == sha
