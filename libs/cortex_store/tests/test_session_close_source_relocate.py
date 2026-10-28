"""Session-close JSONL source relocation at persist."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from cortex_store.raw_transcript_store import DEFAULT_NOHOUSE
from cortex_store.routes.session_close_source_relocate import (
    relocate_transcript_source,
    restore_transcript_source,
)

pytestmark = pytest.mark.offline

SESSION_ID = "cursor-2026-01-01-120000-abc"
TRANSCRIPT_ID = "550e8400-e29b-41d4-a716-446655440000"
DATE = "2026-01-01"


def _relocate(
    tmp_path: Path,
    raw: bytes,
    *,
    house: str | None = None,
    transcript_id: str | None = TRANSCRIPT_ID,
):
    expected = hashlib.sha256(raw).hexdigest()
    dest = tmp_path / f"notes/system/seals/{SESSION_ID}.source.jsonl"
    dest.parent.mkdir(parents=True, exist_ok=True)
    with patch(
        "cortex_store.routes.session_close_source_relocate.transcript_source_relocated",
    ):
        return relocate_transcript_source(
            source_abs_path=dest,
            raw_bytes=raw,
            expected_sha256=expected,
            session_id=SESSION_ID,
            transcript_id=transcript_id,
            files_root=tmp_path,
            house=house,
        )


def test_relocate_writes_seal_and_canonical_raw_default_house(tmp_path: Path) -> None:
    raw = b'{"role":"user","message":{"content":[{"type":"text","text":"hi"}]}}\n'
    result = _relocate(tmp_path, raw)
    seal = tmp_path / f"notes/system/seals/{SESSION_ID}.source.jsonl"
    assert seal.read_bytes() == raw
    assert result.written_sha256 == hashlib.sha256(raw).hexdigest()
    prefix = f"notes/system/transcripts/{DEFAULT_NOHOUSE}/cursor/raw/{DATE}/{TRANSCRIPT_ID}"
    assert result.raw_rel_path == f"{prefix}.jsonl"
    assert result.manifest_rel_path == f"{prefix}.manifest.json"
    assert (tmp_path / result.raw_rel_path).read_bytes() == raw
    manifest = json.loads((tmp_path / result.manifest_rel_path).read_text(encoding="utf-8"))
    assert manifest["source"] == "cursor"
    assert manifest["house"] == DEFAULT_NOHOUSE
    assert "transcript:" in manifest["aliases"][0]


def test_relocate_writes_canonical_raw_with_explicit_house(tmp_path: Path) -> None:
    raw = b"{}\n"
    result = _relocate(tmp_path, raw, house="21012")
    prefix = f"notes/system/transcripts/21012/cursor/raw/{DATE}/{TRANSCRIPT_ID}"
    assert result.raw_rel_path == f"{prefix}.jsonl"
    assert (tmp_path / result.manifest_rel_path).is_file()


def test_relocate_revision_supersedes_on_changed_bytes(tmp_path: Path) -> None:
    v1 = b"export-one\n"
    v2 = b"export-two\n"
    r1 = _relocate(tmp_path, v1)
    r2 = _relocate(tmp_path, v2)
    assert r1.raw_rel_path.endswith(f"{TRANSCRIPT_ID}.jsonl")
    assert r2.raw_rel_path.endswith(f"{TRANSCRIPT_ID}__r2.jsonl")
    assert r2.manifest_rel_path is not None
    manifest = json.loads((tmp_path / r2.manifest_rel_path).read_text(encoding="utf-8"))
    assert manifest["supersedes"] == r1.manifest_rel_path
    assert (tmp_path / r1.raw_rel_path).read_bytes() == v1
    assert (tmp_path / r2.raw_rel_path).read_bytes() == v2


def test_restore_rolls_back_seal_and_raw(tmp_path: Path) -> None:
    raw = b"rollback\n"
    result = _relocate(tmp_path, raw)
    seal = tmp_path / f"notes/system/seals/{SESSION_ID}.source.jsonl"
    assert result.raw_write_result is not None
    restore_transcript_source(
        seal,
        result.prior_snapshot,
        files_root=tmp_path,
        raw_write_result=result.raw_write_result,
    )
    assert not seal.exists()
    assert not (tmp_path / result.raw_rel_path).exists()
    assert not (tmp_path / result.manifest_rel_path).exists()


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
            session_id=SESSION_ID,
            transcript_id=None,
            files_root=tmp_path,
        )
    assert exc.value.status_code == 409
    assert not dest.exists()
    raw_dir = tmp_path / "notes/system/transcripts"
    assert not raw_dir.exists() or not any(raw_dir.rglob("*"))
