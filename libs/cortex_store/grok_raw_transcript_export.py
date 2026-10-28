"""Grok-bot raw transcript export under ``notes/system/transcripts/.../grok-bot/raw/``.

Local Agent-SDK ``--state-root`` session files export as ``status=complete``
(verbatim bytes). Temporal ``ReadTranscript`` harvests are pseudo-raw
(``status=partial``, ``exporter=grok_read_transcript_pseudo``). Platform
byte-exact export for temporal Grok Bots remains open — cortex assertion
``a:38940`` (do not treat partial harvests as byte-exact).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from durable_io.atomic import durable_write_text

from cortex_store.raw_transcript_store import (
    RawTranscriptManifest,
    WriteRawTranscriptResult,
    write_raw_transcript,
)

_SOURCE = "grok-bot"
_KIND = "raw"
_GAP_ASSERTION_ID = 38940
MAX_GROK_STATE_ROOT_SESSION_BYTES = 128 * 1024 * 1024


def _read_bounded_state_root_session(
    session_file: Path,
    *,
    state_root: Path,
    max_bytes: int,
) -> bytes:
    """Read session bytes only when *session_file* lies under *state_root*."""
    if not session_file.is_file():
        raise FileNotFoundError(session_file)
    resolved = session_file.resolve()
    root = state_root.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(
            f"session_file {session_file!r} is not under state_root {state_root!r}"
        ) from exc
    size = resolved.stat().st_size
    if size > max_bytes:
        raise ValueError(
            f"session_file size {size} exceeds max_bytes {max_bytes} for state-root export"
        )
    raw = resolved.read_bytes()
    if len(raw) > max_bytes:
        raise ValueError(
            f"session_file read length {len(raw)} exceeds max_bytes {max_bytes}"
        )
    return raw


def _to_export_dict(
    result: WriteRawTranscriptResult,
    *,
    manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    manifest_dict = manifest if manifest is not None else result.manifest.to_dict()
    return {
        "house": result.manifest.house,
        "native_id": result.manifest.native_id,
        "raw_rel_path": result.raw_rel_path,
        "manifest_rel_path": result.manifest_rel_path,
        "payload_path": result.raw_rel_path,
        "status": result.manifest.status,
        "exporter": result.manifest.exporter,
        "manifest": manifest_dict,
        "wrote_new_raw": result.wrote_new_raw,
    }


def _patch_manifest_extra(
    *,
    files_root: Path,
    manifest_abs: Path,
    extras: dict[str, Any],
) -> dict[str, Any]:
    data = json.loads(manifest_abs.read_text(encoding="utf-8"))
    for key, value in extras.items():
        data[key] = value
    durable_write_text(
        manifest_abs,
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        retain_store_root=files_root,
    )
    return data


def export_grok_local_session_raw(
    *,
    files_root: Path,
    state_root: Path,
    session_file: Path,
    date_str: str,
    house: str | None = None,
    native_id: str | None = None,
    aliases: Sequence[str] = (),
    captured_at: str | None = None,
    max_bytes: int = MAX_GROK_STATE_ROOT_SESSION_BYTES,
) -> dict[str, Any]:
    """Export verbatim Agent-SDK state-root session bytes (``status=complete``)."""
    raw_bytes = _read_bounded_state_root_session(
        session_file, state_root=state_root, max_bytes=max_bytes
    )
    stem = (native_id or session_file.stem).strip()
    native_ext = session_file.suffix.lstrip(".") if session_file.suffix else "json"
    result = write_raw_transcript(
        files_root=files_root,
        source=_SOURCE,
        date_str=date_str,
        native_id=stem,
        native_ext=native_ext,
        raw_bytes=raw_bytes,
        exporter="agent_sdk.state_root",
        exporter_version="1.0",
        aliases=aliases,
        captured_at=captured_at,
        status="complete",
        house=house,
        kind=_KIND,
    )
    manifest = result.manifest.to_dict()
    RawTranscriptManifest.from_dict(manifest)
    return _to_export_dict(result, manifest=manifest)


def export_grok_read_transcript_partial(
    *,
    files_root: Path,
    native_id: str,
    payload_bytes: bytes,
    date_str: str,
    house: str | None = None,
    native_ext: str = "json",
    aliases: Sequence[str] = (),
    captured_at: str | None = None,
) -> dict[str, Any]:
    """Export temporal ReadTranscript harvest (``status=partial``, not byte-exact)."""
    result = write_raw_transcript(
        files_root=files_root,
        source=_SOURCE,
        date_str=date_str,
        native_id=native_id.strip(),
        native_ext=native_ext,
        raw_bytes=payload_bytes,
        exporter="grok_read_transcript_pseudo",
        exporter_version="1.0",
        aliases=aliases,
        captured_at=captured_at,
        status="partial",
        house=house,
        kind=_KIND,
    )
    manifest = _patch_manifest_extra(
        files_root=files_root,
        manifest_abs=result.manifest_abs_path,
        extras={"known_gap_assertion_id": _GAP_ASSERTION_ID},
    )
    RawTranscriptManifest.from_dict(
        {k: manifest[k] for k in manifest if k != "known_gap_assertion_id"}
    )
    return _to_export_dict(result, manifest=manifest)


__all__ = [
    "MAX_GROK_STATE_ROOT_SESSION_BYTES",
    "export_grok_local_session_raw",
    "export_grok_read_transcript_partial",
]
