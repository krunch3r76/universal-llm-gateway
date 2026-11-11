"""Interlocutor text_uri and large-body pointer delivery."""

from __future__ import annotations

from pathlib import Path

import pytest
from interlocutor.text_body import (
    STEER_INLINE_MAX_BYTES,
    pointer_body_for_large_text,
    resolve_interlocutor_text,
)

pytestmark = pytest.mark.offline


def test_inline_over_limit_refuses_text_too_large() -> None:
    big = "x" * (STEER_INLINE_MAX_BYTES + 1)
    out = resolve_interlocutor_text(text=big, text_uri=None)
    assert out["ok"] is False
    assert out["code"] == "text_too_large"


def test_text_uri_delivers_pointer_not_raw_body(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    body = "y" * (STEER_INLINE_MAX_BYTES + 100)
    uri = "cortex://notes/tmp/sample.md"

    sample = tmp_path / "sample.md"
    sample.write_text(body, encoding="utf-8")

    class _Ingress:
        def __init__(self) -> None:
            self.resolved = sample

    monkeypatch.setattr(
        "implement_admission.scheme_resolve.resolve_fs_ingress",
        lambda _uri: _Ingress(),
    )
    out = resolve_interlocutor_text(text=None, text_uri=uri)
    assert out["ok"] is True
    assert out["large_delivered_via_pointer"] is True
    assert len(out["body"].encode("utf-8")) < STEER_INLINE_MAX_BYTES
    assert uri in out["body"]


def test_pointer_template() -> None:
    ptr = pointer_body_for_large_text(source_uri="cortex://x", body_bytes=9000)
    assert "9000" in ptr
    assert "cortex://x" in ptr
