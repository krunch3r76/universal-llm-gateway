"""Read-only Glass MCP transcript export — capped JSONL snapshot to continuity envelope."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from continuity_tape.events import glass_export_failed
from continuity_tape.extract_jsonl import extract_turns_from_jsonl_bytes
from continuity_tape.messages import (
    ContinuityMessagesEnvelope,
    Tools,
    envelope_wire_dict,
)
from cortex_store.transcript_assembly import locate_cursor_transcript_jsonl

DEFAULT_GLASS_EXPORT_MAX_BYTES = 8_388_608
_EXPORT_ORIGIN = "glass_export_mcp"

Completeness = Literal[
    "complete", "partial_live_tail", "truncated_by_limit", "empty"
]

_SUPERVISOR_KEY_PREFIXES = (
    "todo:",
    "plan:",
    "agent-bus:",
    "packet:",
    "friction:",
    "decision:",
)

_RETRYABLE: dict[str, bool] = {
    "transcript_removed_between_locate_and_read": True,
    "internal_error": True,
}


class GlassExportError(Exception):
    """Structured export failure with taxonomy code."""

    def __init__(self, code: str, *, retryable: bool | None = None) -> None:
        self.code = code
        self.retryable = (
            _RETRYABLE.get(code, False) if retryable is None else retryable
        )
        super().__init__(code)


@dataclass(frozen=True)
class SnapshotRead:
    """Result of a single capped fd read with B1 prefix/tail split."""

    complete_prefix: bytes
    tail: bytes
    source_size_bytes: int
    from_byte_offset: int
    cap_hit: bool
    completeness: Completeness


def _transcripts_root() -> Path:
    override = os.environ.get("CURSOR_AGENT_TRANSCRIPTS_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    return (
        Path.home()
        / ".cursor"
        / "projects"
        / "mnt-torus-projects-universal-llm-gateway"
        / "agent-transcripts"
    ).resolve()


def _safe_transcript_id(conversation_uuid: str) -> str | None:
    text = conversation_uuid.strip()
    if not text or text in {".", ".."} or "/" in text or "\\" in text:
        return None
    return text


def _parse_allowlist() -> frozenset[str] | None:
    raw = os.environ.get("GLASS_EXPORT_ALLOWED_AGENTS")
    if raw is None or not str(raw).strip():
        return None
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


def _valid_supervisor_work_key(key: str) -> bool:
    text = key.strip()
    if not text:
        return False
    return any(text.startswith(prefix) for prefix in _SUPERVISOR_KEY_PREFIXES)


def _is_standalone_json_record(suffix: bytes) -> bool:
    if not suffix:
        return False
    try:
        text = suffix.decode("utf-8").strip()
    except UnicodeDecodeError:
        return False
    if not text:
        return False
    try:
        json.loads(text)
    except json.JSONDecodeError:
        return False
    return True


def _prefix_jsonl_malformed(complete_prefix: bytes) -> bool:
    if not complete_prefix:
        return False
    try:
        text = complete_prefix.decode("utf-8")
    except UnicodeDecodeError:
        return True
    for line_no, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            json.loads(stripped)
        except json.JSONDecodeError:
            return True
    return False


def validate_from_byte_offset(
    path: Path,
    from_byte_offset: int,
    source_size_bytes: int,
) -> None:
    if from_byte_offset < 0 or from_byte_offset > source_size_bytes:
        raise GlassExportError("invalid_request")
    if from_byte_offset == 0 or from_byte_offset == source_size_bytes:
        return
    with path.open("rb") as handle:
        handle.seek(from_byte_offset - 1)
        if handle.read(1) == b"\n":
            return
        handle.seek(from_byte_offset)
        if handle.read(1) == b"\n":
            return
    raise GlassExportError("invalid_request")


def _split_snapshot_buffer(
    buf: bytes,
    *,
    cap: int,
    cap_hit: bool,
    from_byte_offset: int,
    source_size_bytes: int,
) -> tuple[bytes, bytes, Completeness]:
    if source_size_bytes == 0:
        return b"", b"", "empty"
    if from_byte_offset == source_size_bytes:
        return b"", b"", "complete"

    if cap_hit:
        window = buf[:cap]
        if b"\n" not in window:
            raise GlassExportError("export_limit_exceeded")
        last_nl = window.rfind(b"\n")
        complete_prefix = window[: last_nl + 1]
        return complete_prefix, b"", "truncated_by_limit"

    if not buf:
        return b"", b"", "complete"

    split_at = buf.rfind(b"\n")
    if split_at < 0:
        line_bounded = b""
        suffix = buf
    else:
        line_bounded = buf[: split_at + 1]
        suffix = buf[split_at + 1 :]

    if _is_standalone_json_record(suffix):
        return line_bounded + suffix, b"", "complete"
    if suffix:
        return line_bounded, suffix, "partial_live_tail"
    return line_bounded, b"", "complete"


def read_jsonl_snapshot_capped(
    path: Path,
    *,
    cap_bytes: int,
    from_byte_offset: int = 0,
) -> SnapshotRead:
    """Read at most ``cap_bytes + 1`` bytes from *path* starting at *from_byte_offset*."""
    if cap_bytes < 1:
        raise GlassExportError("invalid_request")
    try:
        source_size_bytes = path.stat().st_size
    except OSError as exc:
        if exc.errno == 13:
            raise GlassExportError("transcript_unreadable") from exc
        raise GlassExportError("transcript_removed_between_locate_and_read") from exc

    validate_from_byte_offset(path, from_byte_offset, source_size_bytes)

    with path.open("rb") as handle:
        try:
            handle.seek(from_byte_offset)
            buf = handle.read(cap_bytes + 1)
        except OSError as exc:
            if exc.errno == 13:
                raise GlassExportError("transcript_unreadable") from exc
            raise GlassExportError(
                "transcript_removed_between_locate_and_read"
            ) from exc

    cap_hit = len(buf) > cap_bytes

    complete_prefix, tail, completeness = _split_snapshot_buffer(
        buf,
        cap=cap_bytes,
        cap_hit=cap_hit,
        from_byte_offset=from_byte_offset,
        source_size_bytes=source_size_bytes,
    )
    if _prefix_jsonl_malformed(complete_prefix):
        raise GlassExportError("jsonl_malformed")

    return SnapshotRead(
        complete_prefix=complete_prefix,
        tail=tail,
        source_size_bytes=source_size_bytes,
        from_byte_offset=from_byte_offset,
        cap_hit=cap_hit,
        completeness=completeness,
    )


def _relative_jsonl_path(path: Path) -> str:
    root = _transcripts_root()
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return path.name


def _emit_failure(code: str) -> None:
    glass_export_failed(code=code, origin=_EXPORT_ORIGIN)


def _check_transcripts_root() -> None:
    root = _transcripts_root()
    if not root.is_dir() or not os.access(root, os.R_OK):
        raise GlassExportError("transcript_root_unreachable", retryable=False)


def export_glass_transcript_messages(
    *,
    transcript_id: str,
    supervisor_work_key: str,
    trusted_caller: str | None,
    tools: Tools = "marker",
    strict_dialogue: bool = False,
    from_byte_offset: int = 0,
    cap_bytes: int | None = None,
    observed_at: str | None = None,
) -> dict[str, Any]:
    """Core export: authorize, resolve, snapshot-read, extract, provenance."""
    cap = cap_bytes if cap_bytes is not None else DEFAULT_GLASS_EXPORT_MAX_BYTES
  # Validate request fields before auth side effects on malformed keys
    if str(tools) == "full":
        raise GlassExportError("invalid_request")
    if not _valid_supervisor_work_key(supervisor_work_key):
        raise GlassExportError("invalid_request")

    allowlist = _parse_allowlist()
    caller = (trusted_caller or "").strip()
    if allowlist is None or caller not in allowlist:
        _emit_failure("export_unauthorized")
        raise GlassExportError("export_unauthorized")

    if _safe_transcript_id(transcript_id) is None:
        raise GlassExportError("transcript_id_invalid")

    _check_transcripts_root()

    try:
        jsonl_path, locate_code = locate_cursor_transcript_jsonl(transcript_id)
    except OSError:
        raise GlassExportError("transcript_root_unreachable", retryable=False) from None

    if locate_code == "checkpoint.window_ambiguous":
        _emit_failure("transcript_ambiguous")
        raise GlassExportError("transcript_ambiguous")
    if jsonl_path is None:
        _emit_failure("transcript_not_found")
        raise GlassExportError("transcript_not_found")

    try:
        snapshot = read_jsonl_snapshot_capped(
            jsonl_path,
            cap_bytes=cap,
            from_byte_offset=from_byte_offset,
        )
    except GlassExportError as exc:
        _emit_failure(exc.code)
        raise

    now = observed_at or datetime.now(tz=UTC).isoformat()
    prefix = snapshot.complete_prefix
    byte_offset = snapshot.from_byte_offset + len(prefix)
    source_sha256 = hashlib.sha256(prefix).hexdigest()
    next_byte_offset: int | None = (
        byte_offset if snapshot.completeness == "truncated_by_limit" else None
    )

    try:
        envelope: ContinuityMessagesEnvelope = extract_turns_from_jsonl_bytes(
            prefix,
            tools=tools,
            transcript_id=transcript_id,
            observed_at=now,
            surface="glass",
            strict_dialogue=strict_dialogue,
            event_origin=_EXPORT_ORIGIN,
            event_truncated=snapshot.completeness
            not in ("complete", "empty"),
        )
    except ValueError:
        _emit_failure("jsonl_malformed")
        raise GlassExportError("jsonl_malformed") from None
    except Exception:
        _emit_failure("internal_error")
        raise GlassExportError("internal_error", retryable=True) from None

    provenance = {
        "transcript_id": transcript_id,
        "jsonl_path": _relative_jsonl_path(jsonl_path),
        "source_sha256": source_sha256,
        "messages_sha256": envelope.meta.messages_sha256,
        "observed_at": now,
        "byte_offset": byte_offset,
        "source_size_bytes": snapshot.source_size_bytes,
        "next_byte_offset": next_byte_offset,
        "completeness": snapshot.completeness,
        "supervisor_work_key": supervisor_work_key.strip(),
        "export_origin": _EXPORT_ORIGIN,
        "attribution": "self_asserted",
    }
    return {
        "ok": True,
        "envelope": envelope_wire_dict(envelope),
        "provenance": provenance,
    }


def export_glass_transcript_messages_error_response(
    exc: GlassExportError,
) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {"code": exc.code, "retryable": exc.retryable},
    }


__all__ = [
    "DEFAULT_GLASS_EXPORT_MAX_BYTES",
    "Completeness",
    "GlassExportError",
    "SnapshotRead",
    "export_glass_transcript_messages",
    "export_glass_transcript_messages_error_response",
    "read_jsonl_snapshot_capped",
    "validate_from_byte_offset",
]
