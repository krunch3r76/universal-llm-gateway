"""Tests for glass_export_messages snapshot export."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from cursor_bridge.glass_export_messages import (
    DEFAULT_GLASS_EXPORT_MAX_BYTES,
    GlassExportError,
    export_glass_transcript_messages,
    read_jsonl_snapshot_capped,
    validate_from_byte_offset,
)

pytestmark = pytest.mark.offline

_TID = "11111111-2222-3333-4444-555555555555"


def _minimal_jsonl_line() -> bytes:
    rec = {
        "role": "user",
        "message": {"content": [{"type": "text", "text": "hi"}]},
    }
    return (json.dumps(rec) + "\n").encode()


def _write_jsonl(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


@pytest.fixture
def transcripts_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "transcripts"
    root.mkdir()
    monkeypatch.setenv("CURSOR_AGENT_TRANSCRIPTS_ROOT", str(root))
    monkeypatch.setenv("GLASS_EXPORT_ALLOWED_AGENTS", "glass-supervisor")
    return root


def test_snapshot_terminal_no_newline_complete(tmp_path: Path) -> None:
    data = b'{"a":1}'
    path = tmp_path / "t.jsonl"
    path.write_bytes(data)
    snap = read_jsonl_snapshot_capped(path, cap_bytes=1024)
    assert snap.completeness == "complete"
    assert snap.complete_prefix == data
    assert hashlib.sha256(snap.complete_prefix).hexdigest() == hashlib.sha256(
        data
    ).hexdigest()


def test_snapshot_partial_live_tail(tmp_path: Path) -> None:
    data = b'{"a":1}\n{"b":'
    path = tmp_path / "t.jsonl"
    path.write_bytes(data)
    snap = read_jsonl_snapshot_capped(path, cap_bytes=1024)
    assert snap.completeness == "partial_live_tail"
    assert snap.complete_prefix == b'{"a":1}\n'
    assert len(snap.complete_prefix) == 8


def test_snapshot_truncated_by_limit(tmp_path: Path) -> None:
    cap = 120
    line = _minimal_jsonl_line()
    many = line * 20
    path = tmp_path / "big.jsonl"
    path.write_bytes(many)
    snap = read_jsonl_snapshot_capped(path, cap_bytes=cap)
    assert snap.completeness == "truncated_by_limit"
    assert snap.complete_prefix.endswith(b"\n")
    assert len(snap.complete_prefix) <= cap


def test_cap_hit_corrupt_middle_line_jsonl_malformed(tmp_path: Path) -> None:
    cap = 64
    bad = b"{not-json}\n" + _minimal_jsonl_line() * 5
    path = tmp_path / "bad.jsonl"
    path.write_bytes(bad)
    with pytest.raises(GlassExportError) as exc:
        read_jsonl_snapshot_capped(path, cap_bytes=cap)
    assert exc.value.code == "jsonl_malformed"


def test_export_limit_exceeded_first_line(tmp_path: Path) -> None:
    cap = 8
    path = tmp_path / "huge-line.jsonl"
    path.write_bytes(b"x" * 100)
    with pytest.raises(GlassExportError) as exc:
        read_jsonl_snapshot_capped(path, cap_bytes=cap)
    assert exc.value.code == "export_limit_exceeded"


def test_eof_offset_exception_page(tmp_path: Path) -> None:
    data = b'{"a":1}'
    path = tmp_path / "t.jsonl"
    path.write_bytes(data)
    snap = read_jsonl_snapshot_capped(path, cap_bytes=1024, from_byte_offset=7)
    assert snap.completeness == "complete"
    assert snap.complete_prefix == b""


def test_invalid_interior_offset(tmp_path: Path) -> None:
    data = b'{"a":1}\n{"b":2}'
    path = tmp_path / "t.jsonl"
    path.write_bytes(data)
    with pytest.raises(GlassExportError) as exc:
        validate_from_byte_offset(path, 3, len(data))
    assert exc.value.code == "invalid_request"


def test_capped_read_never_exceeds_cap_plus_one(tmp_path: Path) -> None:
    cap = 80
    content = _minimal_jsonl_line() * 50
    path = tmp_path / "t.jsonl"
    path.write_bytes(content)
    reads: list[int] = []

    real_open = Path.open

    def spy_open(self: Path, *args, **kwargs):
        handle = real_open(self, *args, **kwargs)

        real_read = handle.read

        def tracked_read(size: int = -1, /):
            if size > 0:
                reads.append(size)
            return real_read(size)

        handle.read = tracked_read  # type: ignore[method-assign]
        return handle

    with patch.object(Path, "open", spy_open):
        read_jsonl_snapshot_capped(path, cap_bytes=cap)
    assert reads == [cap + 1]


def test_fail_closed_empty_allowlist(
    transcripts_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GLASS_EXPORT_ALLOWED_AGENTS", raising=False)
    monkeypatch.setenv("GLASS_EXPORT_ALLOWED_AGENTS", "")
    with pytest.raises(GlassExportError) as exc:
        export_glass_transcript_messages(
            transcript_id=_TID,
            supervisor_work_key="todo:grok-workflow-glass-supervisor",
            trusted_caller="glass-supervisor",
        )
    assert exc.value.code == "export_unauthorized"


def test_export_success_provenance(transcripts_root: Path) -> None:
    jsonl = transcripts_root / _TID / f"{_TID}.jsonl"
    _write_jsonl(jsonl, _minimal_jsonl_line())
    out = export_glass_transcript_messages(
        transcript_id=_TID,
        supervisor_work_key="todo:grok-workflow-glass-supervisor",
        trusted_caller="glass-supervisor",
        observed_at="2026-01-01T00:00:00+00:00",
    )
    assert out["ok"] is True
    prov = out["provenance"]
    assert prov["export_origin"] == "glass_export_mcp"
    assert prov["supervisor_work_key"] == "todo:grok-workflow-glass-supervisor"
    assert prov["completeness"] == "complete"
    assert prov["source_size_bytes"] == len(_minimal_jsonl_line())
    assert prov["next_byte_offset"] is None


def test_tools_full_invalid_request(transcripts_root: Path) -> None:
    jsonl = transcripts_root / _TID / f"{_TID}.jsonl"
    _write_jsonl(jsonl, _minimal_jsonl_line())
    with pytest.raises(GlassExportError) as exc:
        export_glass_transcript_messages(
            transcript_id=_TID,
            supervisor_work_key="todo:grok-workflow-glass-supervisor",
            trusted_caller="glass-supervisor",
            tools="full",  # type: ignore[arg-type]
        )
    assert exc.value.code == "invalid_request"


def test_transcript_id_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GLASS_EXPORT_ALLOWED_AGENTS", "glass-supervisor")
    with pytest.raises(GlassExportError) as exc:
        export_glass_transcript_messages(
            transcript_id="../escape",
            supervisor_work_key="todo:x",
            trusted_caller="glass-supervisor",
        )
    assert exc.value.code == "transcript_id_invalid"


def test_transcript_ambiguous_maps(
    transcripts_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GLASS_EXPORT_ALLOWED_AGENTS", "glass-supervisor")
    with patch(
        "cursor_bridge.glass_export_messages.locate_cursor_transcript_jsonl",
        return_value=(None, "checkpoint.window_ambiguous"),
    ):
        with pytest.raises(GlassExportError) as exc:
            export_glass_transcript_messages(
                transcript_id=_TID,
                supervisor_work_key="todo:x",
                trusted_caller="glass-supervisor",
            )
    assert exc.value.code == "transcript_ambiguous"


def test_glass_export_failed_event_on_deny(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GLASS_EXPORT_ALLOWED_AGENTS", "allowed-only")
    with patch("cursor_bridge.glass_export_messages.glass_export_failed") as fail:
        with pytest.raises(GlassExportError):
            export_glass_transcript_messages(
                transcript_id=_TID,
                supervisor_work_key="todo:x",
                trusted_caller="wrong",
            )
        fail.assert_called_once()
        assert fail.call_args.kwargs["origin"] == "glass_export_mcp"


def test_empty_file_completeness(transcripts_root: Path) -> None:
    jsonl = transcripts_root / _TID / f"{_TID}.jsonl"
    _write_jsonl(jsonl, b"")
    out = export_glass_transcript_messages(
        transcript_id=_TID,
        supervisor_work_key="todo:x",
        trusted_caller="glass-supervisor",
    )
    assert out["provenance"]["completeness"] == "empty"


def test_default_cap_constant() -> None:
    assert DEFAULT_GLASS_EXPORT_MAX_BYTES == 8_388_608
