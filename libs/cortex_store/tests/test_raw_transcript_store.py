"""Unit tests for canonical raw transcript store helpers."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from cortex_store.raw_transcript_store import (
    RawTranscriptDigestMismatchError,
    raw_manifest_rel_path,
    raw_transcript_rel_path,
    restore_raw_transcript,
    write_raw_transcript,
)

pytestmark = pytest.mark.offline

SOURCE = "cursor"
DATE = "2026-03-15"
NATIVE_ID = "abc123"
EXT = "jsonl"


def test_path_builders_happy_path() -> None:
    assert (
        raw_transcript_rel_path(
            source=SOURCE,
            date_str=DATE,
            native_id=NATIVE_ID,
            native_ext=EXT,
        )
        == f"notes/system/transcripts/{SOURCE}/raw/{DATE}/{NATIVE_ID}.jsonl"
    )
    assert (
        raw_manifest_rel_path(source=SOURCE, date_str=DATE, native_id=NATIVE_ID)
        == f"notes/system/transcripts/{SOURCE}/raw/{DATE}/{NATIVE_ID}.manifest.json"
    )
    assert (
        raw_transcript_rel_path(
            source=SOURCE,
            date_str=DATE,
            native_id=NATIVE_ID,
            native_ext=".md",
        )
        == f"notes/system/transcripts/{SOURCE}/raw/{DATE}/{NATIVE_ID}.md"
    )


@pytest.mark.parametrize(
    ("source", "date_str", "native_id", "native_ext"),
    [
        ("unknown", DATE, NATIVE_ID, EXT),
        (SOURCE, "2026-13-01", NATIVE_ID, EXT),
        (SOURCE, "bad-date", NATIVE_ID, EXT),
        (SOURCE, DATE, "../escape", EXT),
        (SOURCE, DATE, NATIVE_ID, "bad ext"),
    ],
)
def test_path_builders_reject_invalid(
    source: str, date_str: str, native_id: str, native_ext: str
) -> None:
    with pytest.raises(ValueError):
        raw_transcript_rel_path(
            source=source,
            date_str=date_str,
            native_id=native_id,
            native_ext=native_ext,
        )


def _write(
    tmp_path: Path,
    raw_bytes: bytes,
    *,
    aliases: tuple[str, ...] = (),
    expected_sha256: str | None = None,
):
    return write_raw_transcript(
        files_root=tmp_path,
        source=SOURCE,
        date_str=DATE,
        native_id=NATIVE_ID,
        native_ext=EXT,
        raw_bytes=raw_bytes,
        exporter="test-exporter",
        aliases=aliases,
        expected_sha256=expected_sha256,
    )


def test_first_write_creates_base_files(tmp_path: Path) -> None:
    raw = b"first-payload"
    result = _write(tmp_path, raw)
    assert result.wrote_new_raw is True
    assert result.manifest.supersedes is None
    assert result.manifest.native_id == NATIVE_ID
    assert result.raw_rel_path.endswith(f"{NATIVE_ID}.jsonl")
    raw_abs = tmp_path / result.raw_rel_path
    manifest_abs = tmp_path / result.manifest_rel_path
    assert raw_abs.read_bytes() == raw
    data = json.loads(manifest_abs.read_text(encoding="utf-8"))
    assert data["sha256"] == hashlib.sha256(raw).hexdigest()
    assert data["bytes"] == len(raw)
    assert data["status"] == "complete"
    assert data["exporter"] == "test-exporter"
    assert data["aliases"] == []


def test_idempotent_rewrite_merges_aliases(tmp_path: Path) -> None:
    raw = b"same-bytes"
    first = _write(tmp_path, raw, aliases=("a",))
    captured_at = first.manifest.captured_at
    second = _write(tmp_path, raw, aliases=("b", "a"))
    assert second.wrote_new_raw is False
    assert (tmp_path / second.raw_rel_path).read_bytes() == raw
    assert second.manifest.aliases == ["a", "b"]
    assert second.manifest.captured_at == captured_at
    prior_bytes = second.prior_manifest_bytes
    assert prior_bytes is not None
    restore_raw_transcript(second, files_root=tmp_path)
    assert (tmp_path / second.manifest_rel_path).read_bytes() == prior_bytes


def test_changed_bytes_write_revision_chain(tmp_path: Path) -> None:
    v1 = b"version-one"
    v2 = b"version-two"
    v3 = b"version-three"
    r1 = _write(tmp_path, v1)
    r2 = _write(tmp_path, v2)
    r3 = _write(tmp_path, v3)

    base_manifest = (
        f"notes/system/transcripts/{SOURCE}/raw/{DATE}/{NATIVE_ID}.manifest.json"
    )
    r2_manifest = (
        f"notes/system/transcripts/{SOURCE}/raw/{DATE}/{NATIVE_ID}__r2.manifest.json"
    )

    assert r1.raw_rel_path.endswith(f"{NATIVE_ID}.jsonl")
    assert r2.raw_rel_path.endswith(f"{NATIVE_ID}__r2.jsonl")
    assert r3.raw_rel_path.endswith(f"{NATIVE_ID}__r3.jsonl")
    assert r2.manifest.supersedes == base_manifest
    assert r3.manifest.supersedes == r2_manifest

    assert (tmp_path / r1.raw_rel_path).read_bytes() == v1
    assert (tmp_path / r2.raw_rel_path).read_bytes() == v2
    assert (tmp_path / r3.raw_rel_path).read_bytes() == v3


def test_expected_sha256_mismatch_writes_nothing(tmp_path: Path) -> None:
    raw = b"payload"
    with pytest.raises(RawTranscriptDigestMismatchError) as exc:
        _write(tmp_path, raw, expected_sha256="0" * 64)
    assert exc.value.expected_sha256 == "0" * 64
    assert exc.value.actual_sha256 == hashlib.sha256(raw).hexdigest()
    rel_dir = tmp_path / "notes/system/transcripts" / SOURCE / "raw" / DATE
    assert not rel_dir.exists() or not any(rel_dir.iterdir())


def test_restore_unlinks_new_raw_and_manifest(tmp_path: Path) -> None:
    raw = b"rollback-me"
    result = _write(tmp_path, raw)
    raw_abs = tmp_path / result.raw_rel_path
    manifest_abs = tmp_path / result.manifest_rel_path
    assert raw_abs.is_file()
    assert manifest_abs.is_file()
    restore_raw_transcript(result, files_root=tmp_path)
    assert not raw_abs.exists()
    assert not manifest_abs.exists()
