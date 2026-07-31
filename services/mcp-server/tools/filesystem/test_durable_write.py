"""Durable write + verify-after-write regression tests."""

from __future__ import annotations

import base64
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from tools import _durable_write as durable_mod
from tools._durable_write import (
    WriteVerifyError,
    durable_write_text,
    verify_persisted,
)
from tools._hashing import sha256_hex_of_file
from tools.file_editor import perform_edit
from tools.filesystem import _ops_binary as ops_binary
from tools.filesystem import _ops_paths as ops_paths
from tools.filesystem import _ops_search as ops_search
from tools.filesystem import _ops_text as ops_text
from tools.filesystem import _paths as paths


@pytest.fixture
def sandbox_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    root = tmp_path / "files"
    root.mkdir()
    monkeypatch.setattr(paths, "SANDBOX_ROOT", root)
    return root


def test_durable_write_text_fsyncs_file_and_parent_dir(tmp_path: Path) -> None:
    dest = tmp_path / "nested" / "note.md"
    fsync_calls: list[int] = []
    real_fsync = os.fsync

    def _track_fsync(fd: int) -> None:
        fsync_calls.append(fd)
        real_fsync(fd)

    with patch("tools._durable_write.os.fsync", side_effect=_track_fsync):
        written_sha256 = durable_write_text(dest, "hello durable")

    assert dest.read_text(encoding="utf-8") == "hello durable"
    assert written_sha256 == sha256_hex_of_file(dest)
    assert len(fsync_calls) >= 2


def test_verify_persisted_raises_on_mismatch(tmp_path: Path) -> None:
    dest = tmp_path / "note.md"
    dest.write_text("actual", encoding="utf-8")
    with pytest.raises(WriteVerifyError) as exc_info:
        verify_persisted(dest, "0" * 64)
    assert exc_info.value.reason == "write_verify_failed"
    assert exc_info.value.expected_sha256 == "0" * 64


def test_write_file_impl_errors_on_stale_post_write_read(
    sandbox_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ops_text, "record", lambda *_args, **_kwargs: None)
    rel = "notes/spec.md"
    stale_sha = "deadbeef" * 8

    def _stale_verify(dest: Path, expected_sha256: str) -> None:
        raise WriteVerifyError(
            dest,
            expected_sha256=expected_sha256,
            actual_sha256=stale_sha,
        )

    monkeypatch.setattr(ops_text, "verify_persisted", _stale_verify)

    result = ops_text.write_file_impl(rel, "version-one")
    assert result["reason"] == "write_verify_failed"
    assert result["actual_sha256"] == stale_sha
    assert "status" not in result


def test_edit_file_impl_errors_on_stale_post_write_read(
    sandbox_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ops_text, "record", lambda *_args, **_kwargs: None)
    rel = "notes/spec.md"
    (sandbox_root / rel).parent.mkdir(parents=True, exist_ok=True)
    (sandbox_root / rel).write_text("old-target\n", encoding="utf-8")
    stale_sha = "cafebabe" * 8

    def _stale_verify(dest: Path, expected_sha256: str) -> None:
        raise WriteVerifyError(
            dest,
            expected_sha256=expected_sha256,
            actual_sha256=stale_sha,
        )

    monkeypatch.setattr("tools.file_editor.verify_persisted", _stale_verify)

    result = ops_text.edit_file_impl(
        rel,
        "replace",
        "new-content\n",
        target="old-target",
    )
    assert result["reason"] == "write_verify_failed"
    assert result["actual_sha256"] == stale_sha
    assert "status" not in result


def test_perform_edit_raises_on_stale_post_write_read(tmp_path: Path) -> None:
    target = tmp_path / "note.md"
    target.write_text("old-target\n", encoding="utf-8")
    stale_sha = "feedface" * 8

    def _stale_verify(dest: Path, expected_sha256: str) -> None:
        raise WriteVerifyError(
            dest,
            expected_sha256=expected_sha256,
            actual_sha256=stale_sha,
        )

    with patch("tools.file_editor.verify_persisted", side_effect=_stale_verify):
        with pytest.raises(WriteVerifyError) as exc_info:
            perform_edit(
                target,
                "replace",
                "new-content\n",
                target_str="old-target",
            )
    assert exc_info.value.actual_sha256 == stale_sha


def test_write_binary_impl_errors_on_stale_post_write_read(
    sandbox_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ops_binary, "record", lambda *_args, **_kwargs: None)
    stale_sha = "baadf00d" * 8

    def _stale_verify(dest: Path, expected_sha256: str) -> None:
        raise WriteVerifyError(
            dest,
            expected_sha256=expected_sha256,
            actual_sha256=stale_sha,
        )

    monkeypatch.setattr(ops_binary, "verify_persisted", _stale_verify)

    import base64

    result = ops_binary.write_binary_impl(
        "bin/data.bin",
        base64.b64encode(b"payload").decode("ascii"),
    )
    assert result["reason"] == "write_verify_failed"
    assert result["actual_sha256"] == stale_sha


def test_durable_helper_invoked_for_cortex_write(
    sandbox_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ops_text, "record", lambda *_args, **_kwargs: None)
    calls: list[tuple[Path, str]] = []
    real = durable_mod.durable_write_text

    def _track(dest: Path, content: str) -> str:
        calls.append((dest, content))
        return real(dest, content)

    monkeypatch.setattr(ops_text, "durable_write_text", _track)
    ops_text.write_file_impl("notes/new.md", "hello")
    assert calls and calls[0][1] == "hello"


_RECON_THEME_PATH = "notes/system/recon/my-label/{theme}.md"
_CLEAN_RECON_PATH = "notes/system/recon/my-label/actual-theme.md"


def test_write_rejects_unsubstituted_template_token(
    sandbox_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ops_text, "record", lambda *_args, **_kwargs: None)
    with pytest.raises(ValueError, match=r"\{theme\}") as exc_info:
        ops_text.write_file_impl(_RECON_THEME_PATH, "body")
    assert _RECON_THEME_PATH in str(exc_info.value)


def test_write_succeeds_on_fully_rendered_path(
    sandbox_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ops_text, "record", lambda *_args, **_kwargs: None)
    result = ops_text.write_file_impl(_CLEAN_RECON_PATH, "rendered body")
    assert result["status"] == "written"
    assert (sandbox_root / _CLEAN_RECON_PATH).read_text(encoding="utf-8") == "rendered body"


def test_read_list_search_unaffected_by_template_tokens(
    sandbox_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tools._file_helpers import read_file_result as _read_file_result

    monkeypatch.setattr(
        ops_text,
        "read_file_result",
        lambda path, **kwargs: _read_file_result(path, root=sandbox_root, **kwargs),
    )
    monkeypatch.setattr(ops_text, "SANDBOX_ROOT", sandbox_root)
    monkeypatch.setattr(ops_text, "record", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ops_search, "record", lambda *_args, **_kwargs: None)
    rel = _RECON_THEME_PATH
    target = sandbox_root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("existing sidecar\n", encoding="utf-8")

    read_result = ops_text.read_file_impl(rel)
    assert read_result["content"] == "existing sidecar\n"

    list_result = ops_text.list_files_impl("notes/system/recon/my-label")
    assert rel in list_result["files"]

    search_result = ops_search.search_path_impl(rel, "sidecar")
    assert search_result["matches"]


@pytest.mark.parametrize(
    ("impl", "args"),
    [
        (ops_text.edit_file_impl, ("notes/{theme}.md", "append", "tail\n")),
        (
            ops_binary.write_binary_impl,
            ("notes/{theme}.md", base64.b64encode(b"x").decode("ascii")),
        ),
        (
            ops_binary.append_binary_impl,
            ("notes/{theme}.md", base64.b64encode(b"x").decode("ascii")),
        ),
    ],
)
def test_write_ops_reject_template_tokens(
    sandbox_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    impl: object,
    args: tuple,
) -> None:
    monkeypatch.setattr(ops_text, "record", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ops_binary, "record", lambda *_args, **_kwargs: None)
    rel = args[0]
    if impl is ops_text.edit_file_impl:
        target = sandbox_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("head\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"\{theme\}"):
        impl(*args)


def test_edit_prepend_replace_insert_reject_template_tokens(
    sandbox_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ops_text, "record", lambda *_args, **_kwargs: None)
    rel = "notes/{theme}.md"
    target = sandbox_root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("needle\n", encoding="utf-8")

    for op, kwargs in (
        ("prepend", {}),
        ("replace", {"target": "needle"}),
        ("insert_at_line", {"line": 1}),
    ):
        with pytest.raises(ValueError, match=r"\{theme\}"):
            ops_text.edit_file_impl(rel, op, "x\n", **kwargs)


def test_move_copy_reject_template_target_only(
    sandbox_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ops_paths, "record", lambda *_args, **_kwargs: None)
    src_rel = _CLEAN_RECON_PATH
    src = sandbox_root / src_rel
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text("payload\n", encoding="utf-8")

    with pytest.raises(ValueError, match=r"\{theme\}"):
        ops_paths.move_file_impl(src_rel, _RECON_THEME_PATH)
    with pytest.raises(ValueError, match=r"\{theme\}"):
        ops_paths.copy_file_impl(src_rel, _RECON_THEME_PATH)

    dst_rel = "notes/system/recon/my-label/moved-theme.md"
    move_result = ops_paths.move_file_impl(src_rel, dst_rel)
    assert move_result["status"] == "moved"
    assert (sandbox_root / dst_rel).read_text(encoding="utf-8") == "payload\n"


def test_incidental_braces_with_whitespace_not_rejected(
    sandbox_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ops_text, "record", lambda *_args, **_kwargs: None)
    rel = "notes/system/recon/{ not a token }/note.md"
    result = ops_text.write_file_impl(rel, "allowed")
    assert result["status"] == "written"
