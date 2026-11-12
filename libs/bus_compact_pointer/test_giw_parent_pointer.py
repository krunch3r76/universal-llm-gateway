"""GIW parent-bus pointer enrichment (build_parent_bus_pointer)."""

from __future__ import annotations

import hashlib

import pytest

from services.git_integration_worker.cursor_sdk_closeout.closeout_memo_emit import (
    build_parent_bus_pointer,
)

pytestmark = pytest.mark.offline

_PARENT = "23158"
_WORKER = "23159"
_DISPATCH = "disp-giw-parent"
_EXEC = "exec-giw-parent"


def _row_rec(*, sidecar_ref: str | None = "cortex://notes/example.md") -> tuple[dict, dict]:
    row = {
        "wake_lane": None,
        "thread_id": _WORKER,
        "caller_agent": "lead-seat",
        "dispatch_id": _DISPATCH,
        "execution_id": _EXEC,
    }
    memo: dict[str, object] = {"turn": 165}
    if sidecar_ref:
        memo["sidecar_ref"] = sidecar_ref
    rec = {"parent_dispatch_thread_id": _PARENT, "memo_closeout": memo}
    return row, rec


def test_build_parent_bus_pointer_readable_sidecar_sha_and_sections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sidecar_md = "# Closeout\n\n## summary\n\nDone.\n## tests\n\npytest ok.\n"
    ref = "cortex://notes/tmp/giw-parent-sidecar.md"

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.closeout_memo_emit._read_sidecar_text",
        lambda uri: sidecar_md if uri == ref else None,
    )

    row, rec = _row_rec(sidecar_ref=ref)
    result = build_parent_bus_pointer(row, rec, kind="sdk_closeout", status="completed")
    assert result is not None
    _thread, _to, subject, body = result
    assert subject == f"cursor-sdk CLOSEOUT {_DISPATCH} | giw pointer"
    assert len(body) <= 2000
    digest = hashlib.sha256(sidecar_md.encode("utf-8")).hexdigest()
    assert f"sha256: {digest}" in body
    assert "## sections" in body
    assert "kind: sdk_closeout" in body
    assert f"sidecar_ref: {ref}" in body


def test_build_parent_bus_pointer_unreadable_sidecar_sections_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ref = "cortex://notes/missing-sidecar.md"

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.closeout_memo_emit._read_sidecar_text",
        lambda _uri: None,
    )

    row, rec = _row_rec(sidecar_ref=ref)
    result = build_parent_bus_pointer(row, rec, kind="sdk_closeout", status="completed")
    assert result is not None
    body = result[3]
    assert len(body) <= 2000
    assert f"sections: unavailable (md_list {ref})" in body
    assert "sha256:" not in body
    assert "## sections" not in body


def test_build_parent_bus_pointer_no_sidecar_ref_unchanged_base_fields() -> None:
    row, rec = _row_rec(sidecar_ref=None)
    rec["memo_closeout"] = {"turn": 1}
    result = build_parent_bus_pointer(row, rec, kind="sdk_closeout", status="completed")
    assert result is not None
    body = result[3]
    assert "sidecar_ref:" not in body
    assert "sha256:" not in body
    assert "execution_id:" in body
