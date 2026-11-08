"""Tests for glass_export_messages snapshot export."""

from __future__ import annotations

import hashlib
import json
import os
import stat
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


def test_partial_live_tail_provenance_hash_witness(transcripts_root: Path) -> None:
    raw = _minimal_jsonl_line() + b'{"role":'
    jsonl = transcripts_root / _TID / f"{_TID}.jsonl"
    _write_jsonl(jsonl, raw)
    out = export_glass_transcript_messages(
        transcript_id=_TID,
        supervisor_work_key="todo:x",
        trusted_caller="glass-supervisor",
    )
    assert out["provenance"]["completeness"] == "partial_live_tail"
    prefix = _minimal_jsonl_line()
    assert out["provenance"]["byte_offset"] == len(prefix)
    assert out["provenance"]["source_sha256"] == hashlib.sha256(prefix).hexdigest()


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


def test_scalar_json_suffix_is_malformed_not_internal(
    transcripts_root: Path,
) -> None:
    jsonl = transcripts_root / _TID / f"{_TID}.jsonl"
    _write_jsonl(jsonl, b"12\n")
    with pytest.raises(GlassExportError) as exc:
        export_glass_transcript_messages(
            transcript_id=_TID,
            supervisor_work_key="todo:x",
            trusted_caller="glass-supervisor",
        )
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


def test_blank_line_boundary_offset(tmp_path: Path) -> None:
    line1 = _minimal_jsonl_line()
    line2 = _minimal_jsonl_line()
    data = line1 + b"\n" + line2
    path = tmp_path / "t.jsonl"
    path.write_bytes(data)
    offset = len(line1) + 1
    validate_from_byte_offset(path, offset, len(data))
    snap = read_jsonl_snapshot_capped(path, cap_bytes=4096, from_byte_offset=offset)
    assert snap.completeness == "complete"


def test_resume_at_8_and_page_chain(tmp_path: Path) -> None:
    page_a = b'{"a":1}\n'
    rest = _minimal_jsonl_line() * 3
    data = page_a + rest
    path = tmp_path / "t.jsonl"
    path.write_bytes(data)
    cap = 12
    first = read_jsonl_snapshot_capped(path, cap_bytes=cap, from_byte_offset=0)
    assert first.completeness == "truncated_by_limit"
    assert first.complete_prefix == page_a
    resume = 8
    validate_from_byte_offset(path, resume, len(data))
    second = read_jsonl_snapshot_capped(
        path, cap_bytes=500, from_byte_offset=resume
    )
    assert second.completeness in ("truncated_by_limit", "complete")


def test_exact_cap_at_eof_not_truncated(tmp_path: Path) -> None:
    data = b'{"a":1}'
    path = tmp_path / "t.jsonl"
    path.write_bytes(data)
    snap = read_jsonl_snapshot_capped(path, cap_bytes=len(data))
    assert snap.completeness == "complete"
    assert snap.cap_hit is False


def test_concurrent_append_stable_prefix_hash(tmp_path: Path) -> None:
    line = _minimal_jsonl_line()
    path = tmp_path / "grow.jsonl"
    path.write_bytes(line)
    snap_a = read_jsonl_snapshot_capped(path, cap_bytes=4096, from_byte_offset=0)
    path.write_bytes(line + b'{"partial":')
    snap_b = read_jsonl_snapshot_capped(path, cap_bytes=4096, from_byte_offset=0)
    assert hashlib.sha256(snap_a.complete_prefix).digest() == hashlib.sha256(
        snap_b.complete_prefix
    ).digest()


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
    assert max(reads) <= cap + 1


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


def test_trusted_caller_none_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GLASS_EXPORT_ALLOWED_AGENTS", "glass-supervisor")
    with pytest.raises(GlassExportError) as exc:
        export_glass_transcript_messages(
            transcript_id=_TID,
            supervisor_work_key="todo:x",
            trusted_caller=None,
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
    assert prov["transcript_id"] == _TID
    assert prov["completeness"] == "complete"
    assert prov["source_size_bytes"] == len(_minimal_jsonl_line())
    assert prov["next_byte_offset"] is None


def test_success_event_surface_glass(transcripts_root: Path) -> None:
    jsonl = transcripts_root / _TID / f"{_TID}.jsonl"
    _write_jsonl(jsonl, _minimal_jsonl_line())
    with patch("continuity_tape.extract_jsonl.continuity_messages_extracted") as ev:
        export_glass_transcript_messages(
            transcript_id=_TID,
            supervisor_work_key="todo:x",
            trusted_caller="glass-supervisor",
        )
        assert ev.call_args.kwargs["surface"] == "glass"
        assert ev.call_args.kwargs["origin"] == "glass_export_mcp"


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


def test_transcript_id_glob_metacharacters_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GLASS_EXPORT_ALLOWED_AGENTS", "glass-supervisor")
    with pytest.raises(GlassExportError) as exc:
        export_glass_transcript_messages(
            transcript_id="*",
            supervisor_work_key="todo:x",
            trusted_caller="glass-supervisor",
        )
    assert exc.value.code == "transcript_id_invalid"


def test_transcript_id_whitespace_normalized(transcripts_root: Path) -> None:
    jsonl = transcripts_root / _TID / f"{_TID}.jsonl"
    _write_jsonl(jsonl, _minimal_jsonl_line())
    out = export_glass_transcript_messages(
        transcript_id=f"  {_TID}  ",
        supervisor_work_key="todo:x",
        trusted_caller="glass-supervisor",
    )
    assert out["provenance"]["transcript_id"] == _TID


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


def test_emit_failure_best_effort(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GLASS_EXPORT_ALLOWED_AGENTS", "x")
    with patch(
        "cursor_bridge.glass_export_messages.glass_export_failed",
        side_effect=RuntimeError("telemetry down"),
    ):
        with pytest.raises(GlassExportError) as exc:
            export_glass_transcript_messages(
                transcript_id=_TID,
                supervisor_work_key="todo:x",
                trusted_caller="wrong",
            )
        assert exc.value.code == "export_unauthorized"


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


def test_transcript_unreadable_eacces(tmp_path: Path) -> None:
    path = tmp_path / "locked.jsonl"
    path.write_bytes(_minimal_jsonl_line())
    path.chmod(0)
    try:
        if os.geteuid() == 0:
            pytest.skip("root bypasses chmod zero")
        with pytest.raises(GlassExportError) as exc:
            read_jsonl_snapshot_capped(path, cap_bytes=1024)
        assert exc.value.code == "transcript_unreadable"
    finally:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def test_transcript_removed_enoent(tmp_path: Path) -> None:
    path = tmp_path / "gone.jsonl"
    path.write_bytes(b"x\n")
    path.unlink()
    with pytest.raises(GlassExportError) as exc:
        read_jsonl_snapshot_capped(path, cap_bytes=1024)
    assert exc.value.code == "transcript_removed_between_locate_and_read"
    assert exc.value.retryable is True


def test_transcript_root_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "CURSOR_AGENT_TRANSCRIPTS_ROOT", "/nonexistent/transcripts/root"
    )
    monkeypatch.setenv("GLASS_EXPORT_ALLOWED_AGENTS", "glass-supervisor")
    with pytest.raises(GlassExportError) as exc:
        export_glass_transcript_messages(
            transcript_id=_TID,
            supervisor_work_key="todo:x",
            trusted_caller="glass-supervisor",
        )
    assert exc.value.code == "transcript_root_unreachable"
    assert exc.value.retryable is False


def test_array_jsonl_line_jsonl_malformed_not_retryable(
    transcripts_root: Path,
) -> None:
    jsonl = transcripts_root / _TID / f"{_TID}.jsonl"
    _write_jsonl(jsonl, b"[1]\n")
    with pytest.raises(GlassExportError) as exc:
        export_glass_transcript_messages(
            transcript_id=_TID,
            supervisor_work_key="todo:x",
            trusted_caller="glass-supervisor",
        )
    assert exc.value.code == "jsonl_malformed"
    assert exc.value.retryable is False


def test_symlink_escape_outside_transcripts_root_refused(
    transcripts_root: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside.jsonl"
    _write_jsonl(outside, _minimal_jsonl_line())
    link = transcripts_root / _TID / f"{_TID}.jsonl"
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(outside)
    with pytest.raises(GlassExportError) as exc:
        export_glass_transcript_messages(
            transcript_id=_TID,
            supervisor_work_key="todo:x",
            trusted_caller="glass-supervisor",
        )
    assert exc.value.code == "transcript_not_found"


def test_cursor_project_fallback_outside_transcripts_root_allowed(
    transcripts_root: Path, tmp_path: Path
) -> None:
    projects_root = tmp_path / "cursor_projects"
    jsonl = (
        projects_root
        / "satellite-ws"
        / "agent-transcripts"
        / _TID
        / f"{_TID}.jsonl"
    )
    _write_jsonl(jsonl, _minimal_jsonl_line())
    with (
        patch(
            "cursor_bridge.glass_export_messages.locate_cursor_transcript_jsonl",
            return_value=(jsonl, None),
        ),
        patch(
            "cortex_store.transcript_assembly._cursor_projects_root",
            return_value=projects_root,
        ),
    ):
        out = export_glass_transcript_messages(
            transcript_id=_TID,
            supervisor_work_key="todo:x",
            trusted_caller="glass-supervisor",
        )
    assert out["ok"] is True


def test_transcript_not_found_emits(transcripts_root: Path) -> None:
    with patch("cursor_bridge.glass_export_messages.glass_export_failed") as fail:
        with pytest.raises(GlassExportError) as exc:
            export_glass_transcript_messages(
                transcript_id=_TID,
                supervisor_work_key="todo:x",
                trusted_caller="glass-supervisor",
            )
        assert exc.value.code == "transcript_not_found"
        assert fail.call_args.kwargs["code"] == "transcript_not_found"


def test_internal_error_from_unexpected_extract(
    transcripts_root: Path,
) -> None:
    jsonl = transcripts_root / _TID / f"{_TID}.jsonl"
    _write_jsonl(jsonl, _minimal_jsonl_line())
    with patch(
        "cursor_bridge.glass_export_messages.extract_turns_from_jsonl_bytes",
        side_effect=RuntimeError("boom"),
    ):
        with pytest.raises(GlassExportError) as exc:
            export_glass_transcript_messages(
                transcript_id=_TID,
                supervisor_work_key="todo:x",
                trusted_caller="glass-supervisor",
            )
        assert exc.value.code == "internal_error"
        assert exc.value.retryable is True


def test_tool_result_secret_not_in_envelope_tools_none(
    transcripts_root: Path,
) -> None:
    secret = "SUPER_SECRET_TOKEN_XYZ"
    rec = {
        "role": "assistant",
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "content": secret,
                }
            ]
        },
    }
    jsonl = transcripts_root / _TID / f"{_TID}.jsonl"
    _write_jsonl(jsonl, (json.dumps(rec) + "\n").encode())
    out = export_glass_transcript_messages(
        transcript_id=_TID,
        supervisor_work_key="todo:x",
        trusted_caller="glass-supervisor",
        tools="none",
    )
    blob = json.dumps(out["envelope"])
    assert secret not in blob


@pytest.mark.parametrize(
    ("code", "retryable", "factory"),
    [
        ("invalid_request", False, "invalid_supervisor"),
        ("transcript_id_invalid", False, "bad_id"),
        ("export_unauthorized", False, "deny"),
        ("transcript_not_found", False, "missing"),
        ("transcript_ambiguous", False, "ambiguous"),
        ("jsonl_malformed", False, "malformed"),
        ("jsonl_malformed", False, "array_line"),
        ("export_limit_exceeded", False, "limit"),
        ("transcript_root_unreachable", False, "root"),
        ("transcript_unreadable", False, "unreadable"),
        ("transcript_removed_between_locate_and_read", True, "enoent"),
        ("internal_error", True, "internal"),
    ],
)
def test_error_taxonomy_retryable_flags(
    code: str,
    retryable: bool,
    factory: str,
    transcripts_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GLASS_EXPORT_ALLOWED_AGENTS", "glass-supervisor")
    exc: GlassExportError | None = None
    if factory == "invalid_supervisor":
        try:
            export_glass_transcript_messages(
                transcript_id=_TID,
                supervisor_work_key="not-a-key",
                trusted_caller="glass-supervisor",
            )
        except GlassExportError as e:
            exc = e
    elif factory == "bad_id":
        try:
            export_glass_transcript_messages(
                transcript_id="not-uuid",
                supervisor_work_key="todo:x",
                trusted_caller="glass-supervisor",
            )
        except GlassExportError as e:
            exc = e
    elif factory == "deny":
        try:
            export_glass_transcript_messages(
                transcript_id=_TID,
                supervisor_work_key="todo:x",
                trusted_caller=None,
            )
        except GlassExportError as e:
            exc = e
    elif factory == "missing":
        try:
            export_glass_transcript_messages(
                transcript_id=_TID,
                supervisor_work_key="todo:x",
                trusted_caller="glass-supervisor",
            )
        except GlassExportError as e:
            exc = e
    elif factory == "ambiguous":
        with patch(
            "cursor_bridge.glass_export_messages.locate_cursor_transcript_jsonl",
            return_value=(None, "checkpoint.window_ambiguous"),
        ):
            try:
                export_glass_transcript_messages(
                    transcript_id=_TID,
                    supervisor_work_key="todo:x",
                    trusted_caller="glass-supervisor",
                )
            except GlassExportError as e:
                exc = e
    elif factory == "malformed":
        jsonl = transcripts_root / _TID / f"{_TID}.jsonl"
        _write_jsonl(jsonl, b"12\n")
        try:
            export_glass_transcript_messages(
                transcript_id=_TID,
                supervisor_work_key="todo:x",
                trusted_caller="glass-supervisor",
            )
        except GlassExportError as e:
            exc = e
    elif factory == "array_line":
        jsonl = transcripts_root / _TID / f"{_TID}.jsonl"
        _write_jsonl(jsonl, b"[1]\n")
        try:
            export_glass_transcript_messages(
                transcript_id=_TID,
                supervisor_work_key="todo:x",
                trusted_caller="glass-supervisor",
            )
        except GlassExportError as e:
            exc = e
    elif factory == "limit":
        path = tmp_path / "huge.jsonl"
        path.write_bytes(b"x" * 200)
        try:
            read_jsonl_snapshot_capped(path, cap_bytes=8)
        except GlassExportError as e:
            exc = e
    elif factory == "root":
        monkeypatch.setenv(
            "CURSOR_AGENT_TRANSCRIPTS_ROOT", "/no/such/transcripts"
        )
        try:
            export_glass_transcript_messages(
                transcript_id=_TID,
                supervisor_work_key="todo:x",
                trusted_caller="glass-supervisor",
            )
        except GlassExportError as e:
            exc = e
    elif factory == "unreadable":
        path = tmp_path / "x.jsonl"
        path.write_bytes(b"a\n")
        path.chmod(0)
        try:
            if os.geteuid() == 0:
                pytest.skip("root bypasses chmod")
            read_jsonl_snapshot_capped(path, cap_bytes=64)
        except GlassExportError as e:
            exc = e
        finally:
            path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    elif factory == "enoent":
        path = tmp_path / "gone.jsonl"
        path.write_bytes(b"a\n")
        path.unlink()
        try:
            read_jsonl_snapshot_capped(path, cap_bytes=64)
        except GlassExportError as e:
            exc = e
    elif factory == "internal":
        jsonl = transcripts_root / _TID / f"{_TID}.jsonl"
        _write_jsonl(jsonl, _minimal_jsonl_line())
        with patch(
            "cursor_bridge.glass_export_messages.extract_turns_from_jsonl_bytes",
            side_effect=KeyError("unexpected"),
        ):
            try:
                export_glass_transcript_messages(
                    transcript_id=_TID,
                    supervisor_work_key="todo:x",
                    trusted_caller="glass-supervisor",
                )
            except GlassExportError as e:
                exc = e
    assert exc is not None
    assert exc.code == code
    assert exc.retryable is retryable
