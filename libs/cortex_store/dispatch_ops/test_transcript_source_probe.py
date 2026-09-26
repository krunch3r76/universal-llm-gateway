"""transcript_source_probe dispatch op."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from cortex_store.dispatch_ops.ops_transcript_source_probe import (
    _op_transcript_source_probe,
)
from cortex_store.verbatim_succession import build_seal_envelope_meta

pytestmark = pytest.mark.offline


def _minimal_jsonl() -> bytes:
    return (
        b'{"role":"user","message":{"content":[{"type":"text","text":"u"}]}}\n'
        b'{"role":"assistant","message":{"content":[{"type":"text","text":"a"}]}}\n'
    )


def test_probe_missing_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_transcript_source_probe._FILES_ROOT",
        tmp_path,
    )
    sid = "cursor-2026-01-01-120000-abc"
    seal_dir = tmp_path / "notes/system/seals"
    seal_dir.mkdir(parents=True)
    from continuity_tape.extract_jsonl import extract_turns_from_jsonl_bytes

    env = extract_turns_from_jsonl_bytes(_minimal_jsonl(), session_id=sid)
    sealed = build_seal_envelope_meta(
        env,
        session_id=sid,
        tools="marker",
        sealed_at="2026-01-01T12:00:00Z",
    )
    (seal_dir / f"{sid}.messages.json").write_text(
        json.dumps(sealed.model_dump(mode="json", by_alias=True)),
        encoding="utf-8",
    )
    with patch(
        "cortex_store.dispatch_ops.ops_transcript_source_probe.transcript_source_probed",
    ):
        report = _op_transcript_source_probe(session_id=sid)
    assert report["source_present"] is False
    assert report["sha_match"] is False


def test_probe_matching_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_transcript_source_probe._FILES_ROOT",
        tmp_path,
    )
    sid = "cursor-2026-01-01-120000-abc"
    seal_dir = tmp_path / "notes/system/seals"
    seal_dir.mkdir(parents=True)
    raw = _minimal_jsonl()
    from continuity_tape.extract_jsonl import extract_turns_from_jsonl_bytes

    env = extract_turns_from_jsonl_bytes(raw, session_id=sid)
    sealed = build_seal_envelope_meta(
        env,
        session_id=sid,
        tools="marker",
        sealed_at="2026-01-01T12:00:00Z",
    )
    (seal_dir / f"{sid}.messages.json").write_text(
        json.dumps(sealed.model_dump(mode="json", by_alias=True)),
        encoding="utf-8",
    )
    (seal_dir / f"{sid}.source.jsonl").write_bytes(raw)
    with patch(
        "cortex_store.dispatch_ops.ops_transcript_source_probe.transcript_source_probed",
    ):
        report = _op_transcript_source_probe(session_id=sid)
    assert report["source_present"] is True
    assert report["sha_match"] is True
