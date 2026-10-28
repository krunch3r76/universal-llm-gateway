"""Unit tests for canonical raw transcript store helpers."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from durable_io.atomic import PreImageMismatchError, durable_write_bytes

from cortex_store.raw_transcript_store import (
    DEFAULT_NOHOUSE,
    RawTranscriptDigestMismatchError,
    raw_manifest_rel_path,
    raw_transcript_rel_path,
    restore_raw_transcript,
    transcript_artifact_rel_path,
    write_raw_transcript,
)

pytestmark = pytest.mark.offline

SOURCE = "cursor"
DATE = "2026-03-15"
NATIVE_ID = "abc123"
EXT = "jsonl"
PREFIX = f"notes/system/transcripts/{DEFAULT_NOHOUSE}/{SOURCE}/raw/{DATE}"


def test_path_builders_happy_path() -> None:
    assert (
        raw_transcript_rel_path(
            source=SOURCE, date_str=DATE, native_id=NATIVE_ID, native_ext=EXT
        )
        == f"{PREFIX}/{NATIVE_ID}.jsonl"
    )
    assert (
        raw_manifest_rel_path(source=SOURCE, date_str=DATE, native_id=NATIVE_ID)
        == f"{PREFIX}/{NATIVE_ID}.manifest.json"
    )
    house_prefix = f"notes/system/transcripts/12286/{SOURCE}/raw/{DATE}"
    assert (
        raw_transcript_rel_path(
            source=SOURCE,
            date_str=DATE,
            native_id=NATIVE_ID,
            native_ext=EXT,
            house="12286",
        )
        == f"{house_prefix}/{NATIVE_ID}.jsonl"
    )
    assert (
        transcript_artifact_rel_path(
            source=SOURCE, date_str=DATE, native_id=NATIVE_ID, ext="md", house="12286"
        )
        == f"notes/system/transcripts/12286/{SOURCE}/raw/{DATE}/{NATIVE_ID}.md"
    )


@pytest.mark.parametrize(
    ("source", "date_str", "native_id", "native_ext"),
    [
        ("unknown", DATE, NATIVE_ID, EXT),
        (SOURCE, "2026-13-01", NATIVE_ID, EXT),
        (SOURCE, "bad-date", NATIVE_ID, EXT),
        (SOURCE, DATE, "../escape", EXT),
        (SOURCE, DATE, NATIVE_ID, "bad ext"),
        (SOURCE, DATE, "abc__r2", EXT),
        (SOURCE, DATE, "x.manifest", EXT),
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
    house: str | None = None,
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
        house=house,
    )


def test_first_write_creates_base_files(tmp_path: Path) -> None:
    raw = b"first-payload"
    result = _write(tmp_path, raw)
    assert result.wrote_new_raw is True
    assert result.manifest.supersedes is None
    assert result.manifest.native_id == NATIVE_ID
    assert result.manifest.house == DEFAULT_NOHOUSE
    assert result.raw_rel_path == f"{PREFIX}/{NATIVE_ID}.jsonl"
    raw_abs = tmp_path / result.raw_rel_path
    manifest_abs = tmp_path / result.manifest_rel_path
    assert raw_abs.read_bytes() == raw
    data = json.loads(manifest_abs.read_text(encoding="utf-8"))
    assert data["sha256"] == hashlib.sha256(raw).hexdigest()
    assert data["source"] == SOURCE
    assert data["house"] == DEFAULT_NOHOUSE


def test_idempotent_rewrite_merges_aliases(tmp_path: Path) -> None:
    raw = b"same-bytes"
    first = _write(tmp_path, raw, aliases=("a",))
    captured_at = first.manifest.captured_at
    second = _write(tmp_path, raw, aliases=("b", "a"))
    assert second.wrote_new_raw is False
    assert second.manifest.aliases == ["a", "b"]
    assert second.manifest.captured_at == captured_at
    prior_bytes = second.prior_manifest_bytes
    assert prior_bytes is not None
    restore_raw_transcript(second, files_root=tmp_path)
    assert (tmp_path / second.manifest_rel_path).read_bytes() == prior_bytes


def test_changed_bytes_write_revision_chain(tmp_path: Path) -> None:
    v1, v2, v3 = b"version-one", b"version-two", b"version-three"
    r1, r2, r3 = _write(tmp_path, v1), _write(tmp_path, v2), _write(tmp_path, v3)
    base_manifest = f"{PREFIX}/{NATIVE_ID}.manifest.json"
    r2_manifest = f"{PREFIX}/{NATIVE_ID}__r2.manifest.json"
    assert r2.manifest.supersedes == base_manifest
    assert r3.manifest.supersedes == r2_manifest
    assert (tmp_path / r1.raw_rel_path).read_bytes() == v1
    assert (tmp_path / r2.raw_rel_path).read_bytes() == v2
    assert (tmp_path / r3.raw_rel_path).read_bytes() == v3


def test_messages_kind_independent_of_raw_revision(tmp_path: Path) -> None:
    raw_bytes, msg_bytes = b"raw-payload", b"messages-payload"
    r_raw = _write(tmp_path, raw_bytes)
    msg_prefix = f"notes/system/transcripts/{DEFAULT_NOHOUSE}/{SOURCE}/messages/{DATE}/{NATIVE_ID}"
    r_msg = write_raw_transcript(
        files_root=tmp_path,
        source=SOURCE,
        date_str=DATE,
        native_id=NATIVE_ID,
        native_ext=EXT,
        raw_bytes=msg_bytes,
        exporter="test-exporter",
        kind="messages",
    )
    assert "/raw/" in r_raw.raw_rel_path
    assert r_msg.raw_rel_path == f"{msg_prefix}.jsonl"
    assert r_msg.manifest_rel_path == f"{msg_prefix}.manifest.json"
    assert "__r2" not in r_msg.raw_rel_path
    assert r_msg.manifest.supersedes is None
    assert (tmp_path / r_raw.raw_rel_path).read_bytes() == raw_bytes
    assert (tmp_path / r_msg.raw_rel_path).read_bytes() == msg_bytes


def test_expected_sha256_mismatch_writes_nothing(tmp_path: Path) -> None:
    raw = b"payload"
    with pytest.raises(RawTranscriptDigestMismatchError):
        _write(tmp_path, raw, expected_sha256="0" * 64)
    rel_dir = tmp_path / "notes/system/transcripts" / DEFAULT_NOHOUSE / SOURCE / "raw" / DATE
    assert not rel_dir.exists() or not any(rel_dir.iterdir())


def test_restore_unlinks_new_raw_and_manifest(tmp_path: Path) -> None:
    result = _write(tmp_path, b"rollback-me")
    raw_abs = tmp_path / result.raw_rel_path
    manifest_abs = tmp_path / result.manifest_rel_path
    restore_raw_transcript(result, files_root=tmp_path)
    assert not raw_abs.exists()
    assert not manifest_abs.exists()


def test_b1_cas_advances_when_concurrent_bytes_differ(tmp_path: Path) -> None:
    v1, v2 = b"concurrent-one", b"concurrent-two"
    target = tmp_path / raw_transcript_rel_path(
        source=SOURCE, date_str=DATE, native_id=NATIVE_ID, native_ext=EXT
    )
    real = durable_write_bytes
    attempts = 0

    def cas_race(dest: Path, data: bytes, **kwargs: object) -> str:
        nonlocal attempts
        attempts += 1
        if dest == target and kwargs.get("expected_pre_image") == "" and attempts == 1:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(v1)
            raise PreImageMismatchError(
                dest, expected_sha256="", actual_sha256=hashlib.sha256(v1).hexdigest()
            )
        return real(dest, data, **kwargs)  # type: ignore[arg-type]

    with patch("cortex_store.raw_transcript_store.durable_write_bytes", side_effect=cas_race):
        result = _write(tmp_path, v2)
    assert result.raw_rel_path == f"{PREFIX}/{NATIVE_ID}__r2.jsonl"
    assert (tmp_path / target).read_bytes() == v1
    assert (tmp_path / result.raw_abs_path).read_bytes() == v2


def test_b1_cas_reconciles_when_concurrent_bytes_match(tmp_path: Path) -> None:
    raw = b"same-concurrent"
    target = tmp_path / raw_transcript_rel_path(
        source=SOURCE, date_str=DATE, native_id=NATIVE_ID, native_ext=EXT
    )
    real = durable_write_bytes

    def cas_same(dest: Path, data: bytes, **kwargs: object) -> str:
        if dest == target and kwargs.get("expected_pre_image") == "":
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(raw)
            raise PreImageMismatchError(
                dest,
                expected_sha256="",
                actual_sha256=hashlib.sha256(raw).hexdigest(),
            )
        return real(dest, data, **kwargs)  # type: ignore[arg-type]

    with patch("cortex_store.raw_transcript_store.durable_write_bytes", side_effect=cas_same):
        result = _write(tmp_path, raw, aliases=("x",))
    assert result.wrote_new_raw is False
    assert result.manifest.aliases == ["x"]


def test_b2_missing_manifest_preserves_supersedes_on_reconcile(tmp_path: Path) -> None:
    v1, v2 = b"base-bytes", b"rev2-bytes"
    _write(tmp_path, v1)
    r2_raw = tmp_path / f"{PREFIX}/{NATIVE_ID}__r2.jsonl"
    r2_raw.parent.mkdir(parents=True, exist_ok=True)
    r2_raw.write_bytes(v2)
    base_manifest = f"{PREFIX}/{NATIVE_ID}.manifest.json"
    result = _write(tmp_path, v2)
    assert result.manifest.supersedes == base_manifest
    assert result.wrote_new_raw is False


def test_b3_native_id_revision_suffix_rejected() -> None:
    with pytest.raises(ValueError):
        raw_transcript_rel_path(
            source=SOURCE, date_str=DATE, native_id="foo__r99", native_ext=EXT
        )
