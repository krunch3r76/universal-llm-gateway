"""Session-close JSONL source relocation at persist."""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from cortex_store.routes.session_close_source_relocate import relocate_transcript_source

pytestmark = pytest.mark.offline


def test_relocate_writes_matching_bytes(tmp_path: Path) -> None:
    raw = b'{"role":"user","message":{"content":[{"type":"text","text":"hi"}]}}\n'
    expected = hashlib.sha256(raw).hexdigest()
    dest = tmp_path / "notes/system/seals/sid.source.jsonl"
    dest.parent.mkdir(parents=True)
    with patch(
        "cortex_store.routes.session_close_source_relocate.transcript_source_relocated",
    ):
        result = relocate_transcript_source(
            source_abs_path=dest,
            raw_bytes=raw,
            expected_sha256=expected,
            session_id="cursor-2026-01-01-120000-abc",
            transcript_id="550e8400-e29b-41d4-a716-446655440000",
            files_root=tmp_path,
        )
    assert dest.read_bytes() == raw
    assert result.written_sha256 == expected


def test_relocate_refuses_digest_mismatch(tmp_path: Path) -> None:
    raw = b"{}\n"
    dest = tmp_path / "notes/system/seals/sid.source.jsonl"
    dest.parent.mkdir(parents=True)
    with (
        patch(
            "cortex_store.routes.session_close_source_relocate.transcript_source_relocate_refused",
        ),
        pytest.raises(HTTPException) as exc,
    ):
        relocate_transcript_source(
            source_abs_path=dest,
            raw_bytes=raw,
            expected_sha256="0" * 64,
            session_id="cursor-2026-01-01-120000-abc",
            transcript_id=None,
            files_root=tmp_path,
        )
    assert exc.value.status_code == 409
    assert not dest.exists()
