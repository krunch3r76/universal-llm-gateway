"""Read-only Glass MCP transcript export — capped JSONL snapshot to continuity envelope."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
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
from cortex_store.transcript_assembly import (
    _is_cursor_project_transcript,
    locate_cursor_transcript_jsonl,
)

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

_TRANSCRIPT_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
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
    if "\x00" in conversation_uuid:
        return None
    text = conversation_uuid.strip()
    if not text or not _TRANSCRIPT_ID_RE.fullmatch(text):
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


def _parsed_json_object(text: str) -> bool:
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, RecursionError):
        return False
    return isinstance(parsed, (dict, list))


def _parsed_json_line_record(text: str) -> bool:
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, RecursionError):
        return False
    return isinstance(parsed, dict)


def _is_standalone_json_record(suffix: bytes) -> bool:
    if not suffix:
        return False
    try:
        text = suffix.decode("utf-8").strip()
    except UnicodeDecodeError:
        return False
    if not text:
        return False
    return _parsed_json_object(text)


def _prefix_jsonl_malformed(complete_prefix: bytes) -> bool:
    if not complete_prefix:
        return False
    try:
        text = complete_prefix.decode("utf-8")
    except UnicodeDecodeError:
        return True
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if not _parsed_json_line_record(stripped):
            return True
    return False


def _map_read_oserror(exc: OSError) -> GlassExportError:
    if exc.errno == errno.EACCES:
        return GlassExportError("transcript_unreadable")
    if exc.errno == errno.ENOENT:
        return GlassExportError("transcript_removed_between_locate_and_read")
    return GlassExportError("internal_error", retryable=True)


def _validate_offset_on_handle(
    handle: Any,
    from_byte_offset: int,
    source_size_bytes: int,
) -> None:
    if from_byte_offset < 0 or from_byte_offset > source_size_bytes:
        raise GlassExportError("invalid_request")
    if from_byte_offset == 0 or from_byte_offset == source_size_bytes:
        return
    handle.seek(from_byte_offset - 1)
    if handle.read(1) == b"\n":
        return
    handle.seek(from_byte_offset)
    if handle.read(1) == b"\n":
        return
    raise GlassExportError("invalid_request")


def validate_from_byte_offset(
    path: Path,
    from_byte_offset: int,
    source_size_bytes: int,
) -> None:
    """Alignment probe for pagination offsets (opens the file once)."""
    try:
        with path.open("rb") as handle:
            _validate_offset_on_handle(handle, from_byte_offset, source_size_bytes)
    except GlassExportError:
        raise
    except OSError as exc:
        raise _map_read_oserror(exc) from exc


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
    if suffix.strip():
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
        with path.open("rb") as handle:
            source_size_bytes = os.fstat(handle.fileno()).st_size
            _validate_offset_on_handle(
                handle, from_byte_offset, source_size_bytes
            )
            handle.seek(from_byte_offset)
            remaining = max(0, source_size_bytes - from_byte_offset)
            to_read = min(cap_bytes + 1, remaining)
            buf = handle.read(to_read)
    except GlassExportError:
        raise
    except OSError as exc:
        raise _map_read_oserror(exc) from exc

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
    try:
        glass_export_failed(code=code, origin=_EXPORT_ORIGIN)
    except Exception:
        pass


def _resolve_allowed_jsonl_path(path: Path) -> Path:
    """Resolve once and return the path that passed containment (TOCTOU-safe read target)."""
    resolved = path.resolve()
    if _is_cursor_project_transcript(resolved):
        return resolved
    root = _transcripts_root()
    try:
        resolved.relative_to(root)
    except ValueError:
        raise GlassExportError("transcript_not_found") from None
    return resolved


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
    try:
        return _export_glass_transcript_messages_body(
            transcript_id=transcript_id,
            supervisor_work_key=supervisor_work_key,
            trusted_caller=trusted_caller,
            tools=tools,
            strict_dialogue=strict_dialogue,
            from_byte_offset=from_byte_offset,
            cap_bytes=cap_bytes,
            observed_at=observed_at,
        )
    except GlassExportError:
        raise
    except Exception:
        _emit_failure("internal_error")
        raise GlassExportError("internal_error", retryable=True) from None


def _export_glass_transcript_messages_body(
    *,
    transcript_id: str,
    supervisor_work_key: str,
    trusted_caller: str | None,
    tools: Tools,
    strict_dialogue: bool,
    from_byte_offset: int,
    cap_bytes: int | None,
    observed_at: str | None,
) -> dict[str, Any]:
    cap = cap_bytes if cap_bytes is not None else DEFAULT_GLASS_EXPORT_MAX_BYTES
    if str(tools) == "full":
        _emit_failure("invalid_request")
        raise GlassExportError("invalid_request")
    if not _valid_supervisor_work_key(supervisor_work_key):
        _emit_failure("invalid_request")
        raise GlassExportError("invalid_request")

    allowlist = _parse_allowlist()
    caller = (trusted_caller or "").strip()
    if allowlist is None or caller not in allowlist:
        _emit_failure("export_unauthorized")
        raise GlassExportError("export_unauthorized")

    normalized_id = _safe_transcript_id(transcript_id)
    if normalized_id is None:
        _emit_failure("transcript_id_invalid")
        raise GlassExportError("transcript_id_invalid")

    try:
        _check_transcripts_root()
    except GlassExportError as exc:
        _emit_failure(exc.code)
        raise

    try:
        jsonl_path, locate_code = locate_cursor_transcript_jsonl(normalized_id)
    except OSError:
        _emit_failure("transcript_root_unreachable")
        raise GlassExportError("transcript_root_unreachable", retryable=False) from None
    except ValueError:
        _emit_failure("transcript_id_invalid")
        raise GlassExportError("transcript_id_invalid") from None

    if locate_code == "checkpoint.window_ambiguous":
        _emit_failure("transcript_ambiguous")
        raise GlassExportError("transcript_ambiguous")
    if jsonl_path is None:
        _emit_failure("transcript_not_found")
        raise GlassExportError("transcript_not_found")
    try:
        resolved_jsonl = _resolve_allowed_jsonl_path(jsonl_path)
    except GlassExportError as exc:
        _emit_failure(exc.code)
        raise

    try:
        snapshot = read_jsonl_snapshot_capped(
            resolved_jsonl,
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
            transcript_id=normalized_id,
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
    except GlassExportError:
        raise
    except Exception:
        _emit_failure("internal_error")
        raise GlassExportError("internal_error", retryable=True) from None

    provenance = {
        "transcript_id": normalized_id,
        "jsonl_path": _relative_jsonl_path(resolved_jsonl),
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
