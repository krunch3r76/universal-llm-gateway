"""Tests for recon sidecar dispatch op."""

from __future__ import annotations

import hashlib

import pytest

from cortex_store.dispatch_ops import _recon_sidecar as recon_mod
from cortex_store.dispatch_ops.ops_misc import _op_recon_sidecar_write


@pytest.mark.offline
def test_recon_sidecar_write_op(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(recon_mod, "_FILES_ROOT", tmp_path)
    content = "# Theme: auth\n\nResults here."
    result = _op_recon_sidecar_write(
        label="thread-3422",
        theme="authentication",
        body=content,
        scopes=["research"],
        queries=["auth middleware"],
        sink_backend="cortex",
    )
    assert "error" not in result
    assert result["uri"] == (
        "cortex://notes/system/recon/thread-3422/authentication.md"
    )
    assert result["body_chars"] == len(content)
    assert result["sha256"] == hashlib.sha256(content.encode("utf-8")).hexdigest()
    written = (
        tmp_path / "notes/system/recon/thread-3422/authentication.md"
    ).read_text(encoding="utf-8")
    assert "label: thread-3422" in written
    assert "theme: authentication" in written
    assert "sink_backend: cortex" in written
    assert "scopes:" in written
    assert "queries:" in written
    assert written.endswith(content)


@pytest.mark.offline
def test_recon_sidecar_traversal_label_rejected(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(recon_mod, "_FILES_ROOT", tmp_path)
    monkeypatch.setattr(
        recon_mod,
        "resolve_recon_target",
        lambda *_args, **_kwargs: None,
    )
    result = _op_recon_sidecar_write(
        label="../../etc/passwd",
        theme="x",
        body="body",
    )
    assert result["error"] == "unsafe recon sidecar path"
