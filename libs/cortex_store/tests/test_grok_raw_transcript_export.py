"""Unit tests for grok-bot raw transcript export (canonical write_raw_transcript)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cortex_store.grok_raw_transcript_export import (
    export_grok_local_session_raw,
    export_grok_read_transcript_partial,
)
from cortex_store.raw_transcript_store import RawTranscriptManifest

pytestmark = pytest.mark.offline

_HOUSE = "lane-21025"
_DATE = "2026-10-27"
_NATIVE = "sess-abc123"
_PREFIX = f"notes/system/transcripts/{_HOUSE}/grok-bot/raw/{_DATE}"


def test_export_grok_local_session_raw_complete(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    session = state_root / f"{_NATIVE}.ndjson"
    session.parent.mkdir(parents=True)
    raw = b'{"role":"user","content":"hi"}\n'
    session.write_bytes(raw)
    result = export_grok_local_session_raw(
        files_root=tmp_path,
        state_root=state_root,
        session_file=session,
        date_str=_DATE,
        house=_HOUSE,
        native_id=_NATIVE,
        captured_at="2026-10-27T12:00:00Z",
    )
    raw_rel = f"{_PREFIX}/{_NATIVE}.ndjson"
    manifest_rel = f"{_PREFIX}/{_NATIVE}.manifest.json"
    assert result["raw_rel_path"] == raw_rel
    assert result["status"] == "complete"
    assert result["exporter"] == "agent_sdk.state_root"
    assert (tmp_path / raw_rel).read_bytes() == raw
    manifest = json.loads((tmp_path / manifest_rel).read_text(encoding="utf-8"))
    assert manifest == RawTranscriptManifest.from_dict(manifest).to_dict()
    assert manifest["status"] == "complete"
    assert manifest["exporter"] == "agent_sdk.state_root"
    assert "session_file" not in manifest
    assert str(session) not in json.dumps(manifest)


def test_export_grok_read_transcript_partial(tmp_path: Path) -> None:
    payload = b'{"turns":[]}'
    result = export_grok_read_transcript_partial(
        files_root=tmp_path,
        native_id=_NATIVE,
        payload_bytes=payload,
        date_str=_DATE,
        house=_HOUSE,
        captured_at="2026-10-27T12:00:00Z",
    )
    raw_rel = f"{_PREFIX}/{_NATIVE}.json"
    manifest_rel = f"{_PREFIX}/{_NATIVE}.manifest.json"
    assert result["raw_rel_path"] == raw_rel
    assert result["status"] == "partial"
    assert result["exporter"] == "grok_read_transcript_pseudo"
    manifest = json.loads((tmp_path / manifest_rel).read_text(encoding="utf-8"))
    assert manifest["status"] == "partial"
    assert manifest["exporter"] == "grok_read_transcript_pseudo"
    assert manifest["known_gap_assertion_id"] == 38940
    RawTranscriptManifest.from_dict(
        {k: v for k, v in manifest.items() if k != "known_gap_assertion_id"}
    )


def test_cas_supersedes_r2_on_byte_change(tmp_path: Path) -> None:
    first = b'{"v":1}'
    second = b'{"v":2}'
    export_grok_read_transcript_partial(
        files_root=tmp_path,
        native_id=_NATIVE,
        payload_bytes=first,
        date_str=_DATE,
        house=_HOUSE,
        captured_at="2026-10-27T12:00:00Z",
    )
    result = export_grok_read_transcript_partial(
        files_root=tmp_path,
        native_id=_NATIVE,
        payload_bytes=second,
        date_str=_DATE,
        house=_HOUSE,
        captured_at="2026-10-27T12:01:00Z",
    )
    assert (tmp_path / f"{_PREFIX}/{_NATIVE}.json").read_bytes() == first
    r2_raw = f"{_PREFIX}/{_NATIVE}__r2.json"
    r2_manifest = f"{_PREFIX}/{_NATIVE}__r2.manifest.json"
    assert result["raw_rel_path"] == r2_raw
    manifest = json.loads((tmp_path / r2_manifest).read_text(encoding="utf-8"))
    assert manifest["supersedes"] == f"{_PREFIX}/{_NATIVE}.manifest.json"


def test_cas_idempotent_when_bytes_match(tmp_path: Path) -> None:
    payload = b'{"same":true}'
    first = export_grok_read_transcript_partial(
        files_root=tmp_path,
        native_id=_NATIVE,
        payload_bytes=payload,
        date_str=_DATE,
        house=_HOUSE,
        captured_at="2026-10-27T12:00:00Z",
    )
    second = export_grok_read_transcript_partial(
        files_root=tmp_path,
        native_id=_NATIVE,
        payload_bytes=payload,
        date_str=_DATE,
        house=_HOUSE,
        captured_at="2026-10-27T12:05:00Z",
    )
    assert second["native_id"] == _NATIVE
    assert second["raw_rel_path"] == first["raw_rel_path"]
    assert not (tmp_path / f"{_PREFIX}/{_NATIVE}__r2.json").exists()


def test_idempotent_reexport_merges_aliases(tmp_path: Path) -> None:
    payload = b'{"same":true}'
    export_grok_read_transcript_partial(
        files_root=tmp_path,
        native_id=_NATIVE,
        payload_bytes=payload,
        date_str=_DATE,
        house=_HOUSE,
        aliases=("alias-a",),
        captured_at="2026-10-27T12:00:00Z",
    )
    export_grok_read_transcript_partial(
        files_root=tmp_path,
        native_id=_NATIVE,
        payload_bytes=payload,
        date_str=_DATE,
        house=_HOUSE,
        aliases=("alias-b", "alias-a"),
        captured_at="2026-10-27T12:05:00Z",
    )
    manifest = json.loads(
        (tmp_path / f"{_PREFIX}/{_NATIVE}.manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["aliases"] == ["alias-a", "alias-b"]
    assert manifest["known_gap_assertion_id"] == 38940


def test_rejects_invalid_native_ext_traversal(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="native_ext"):
        export_grok_read_transcript_partial(
            files_root=tmp_path,
            native_id=_NATIVE,
            payload_bytes=b"{}",
            date_str=_DATE,
            house=_HOUSE,
            native_ext="json/../../x",
        )


def test_rejects_session_file_outside_state_root(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    state_root.mkdir()
    outside = tmp_path / "outside.ndjson"
    outside.write_bytes(b"{}")
    with pytest.raises(ValueError, match="state_root"):
        export_grok_local_session_raw(
            files_root=tmp_path,
            state_root=state_root,
            session_file=outside,
            date_str=_DATE,
            house=_HOUSE,
            native_id=_NATIVE,
        )


def test_rejects_session_file_over_max_bytes(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    session = state_root / f"{_NATIVE}.ndjson"
    session.parent.mkdir(parents=True)
    session.write_bytes(b"x" * 64)
    with pytest.raises(ValueError, match="max_bytes"):
        export_grok_local_session_raw(
            files_root=tmp_path,
            state_root=state_root,
            session_file=session,
            date_str=_DATE,
            house=_HOUSE,
            native_id=_NATIVE,
            max_bytes=32,
        )
    assert not (tmp_path / "notes").exists()


def test_rejects_invalid_date_str(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        export_grok_read_transcript_partial(
            files_root=tmp_path,
            native_id=_NATIVE,
            payload_bytes=b"{}",
            date_str="2026-13-01",
            house=_HOUSE,
        )
