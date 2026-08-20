"""Tests for thread sidecar dispatch op."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from cortex_store.dispatch_ops import _thread_sidecar as sidecar_mod
from cortex_store.dispatch_ops.ops_misc import _op_thread_sidecar_write


@pytest.mark.offline
def test_thread_sidecar_write_op(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sidecar_mod, "_FILES_ROOT", tmp_path)
    content = "# Findings\n\nDetailed analysis here."
    result = _op_thread_sidecar_write(
        thread="1670",
        subject="on-behalf-auto-sidecar review",
        content=content,
        from_agent="dispatch",
        execution_id="exec-test-1",
        oversized=False,
    )
    assert "error" not in result
    assert result["uri"] == (
        "cortex://notes/system/threads/1670-on-behalf-auto-sidecar-review.md"
    )
    assert result["body_chars"] == len(content)
    assert result["sha256"] == hashlib.sha256(content.encode("utf-8")).hexdigest()
    written = (
        tmp_path / "notes/system/threads/1670-on-behalf-auto-sidecar-review.md"
    ).read_text(encoding="utf-8")
    assert written.endswith(content)
    body_after_fm = written.split("---", 2)[-1].lstrip("\n")
    assert hashlib.sha256(body_after_fm.encode("utf-8")).hexdigest() == result["sha256"]


@pytest.mark.offline
def test_thread_sidecar_retain_populates_content_store(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sidecar_mod, "_FILES_ROOT", tmp_path)
    first = sidecar_mod.write_thread_sidecar("1", "retain", "first-body\n")
    second = sidecar_mod.write_thread_sidecar("1", "retain", "second-body\n")
    assert first == second
    first_digest = hashlib.sha256(b"first-body\n").hexdigest()
    store = tmp_path / ".content-store" / "sha256" / first_digest[:2] / first_digest
    assert store.is_file()
    assert store.read_bytes() == b"first-body\n"
    assert Path(second).read_text(encoding="utf-8") == "second-body\n"
