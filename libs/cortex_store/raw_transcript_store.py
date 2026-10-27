"""Canonical raw transcript path builders, manifest schema, and write-once persistence."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from durable_io.atomic import durable_write_bytes, durable_write_text

ALLOWED_RAW_SOURCES: tuple[str, ...] = ("cursor", "grok-bot", "claude-cowork", "claude-chat")
RawTranscriptSource = Literal["cursor", "grok-bot", "claude-cowork", "claude-chat"]
RawTranscriptStatus = Literal["complete", "partial"]
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_EXT_RE = re.compile(r"^[A-Za-z0-9]+$")
_MANIFEST_KEYS = (
    "surface", "exporter", "exporter_version", "native_id", "raw_rel_path",
    "aliases", "captured_at", "bytes", "sha256", "status", "supersedes",
)


def _validate_common(source: str, date_str: str, native_id: str) -> None:
    if source not in ALLOWED_RAW_SOURCES:
        raise ValueError(f"invalid raw transcript source: {source!r}")
    if not _DATE_RE.match(date_str):
        raise ValueError(f"invalid date_str: {date_str!r}")
    datetime.strptime(date_str, "%Y-%m-%d")
    if (
        not native_id
        or ".." in native_id
        or any(ch in native_id for ch in "/\\")
        or any(ch.isspace() or ord(ch) < 32 for ch in native_id)
    ):
        raise ValueError(f"invalid native_id: {native_id!r}")


def _normalize_ext(native_ext: str) -> str:
    ext = native_ext.lstrip(".")
    if not ext or not _EXT_RE.match(ext):
        raise ValueError(f"invalid native_ext: {native_ext!r}")
    return ext


def raw_transcript_rel_path(
    *, source: str, date_str: str, native_id: str, native_ext: str
) -> str:
    """Return the store-relative path for a raw transcript blob."""
    _validate_common(source, date_str, native_id)
    return (
        f"notes/system/transcripts/{source}/raw/{date_str}/"
        f"{native_id}.{_normalize_ext(native_ext)}"
    )


def raw_manifest_rel_path(*, source: str, date_str: str, native_id: str) -> str:
    """Return the store-relative path for a raw transcript sidecar manifest."""
    _validate_common(source, date_str, native_id)
    return f"notes/system/transcripts/{source}/raw/{date_str}/{native_id}.manifest.json"


@dataclass(frozen=True)
class RawTranscriptManifest:
    """Sidecar metadata for one immutable raw transcript blob."""

    surface: str
    exporter: str
    exporter_version: str
    native_id: str
    raw_rel_path: str
    aliases: list[str]
    captured_at: str
    bytes: int
    sha256: str
    status: str
    supersedes: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in _MANIFEST_KEYS}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n"

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RawTranscriptManifest:
        sup = data.get("supersedes")
        return cls(
            str(data["surface"]), str(data["exporter"]), str(data["exporter_version"]),
            str(data["native_id"]), str(data["raw_rel_path"]), [str(a) for a in data["aliases"]],
            str(data["captured_at"]), int(data["bytes"]), str(data["sha256"]), str(data["status"]),
            None if sup is None else str(sup),
        )


class RawTranscriptDigestMismatchError(ValueError):
    """Raised when caller-supplied expected_sha256 does not match raw_bytes."""

    def __init__(self, *, expected_sha256: str, actual_sha256: str) -> None:
        self.expected_sha256, self.actual_sha256 = expected_sha256, actual_sha256
        super().__init__(f"digest mismatch: expected {expected_sha256}, got {actual_sha256}")


@dataclass(frozen=True)
class WriteRawTranscriptResult:
    raw_rel_path: str
    manifest_rel_path: str
    raw_abs_path: Path
    manifest_abs_path: Path
    manifest: RawTranscriptManifest
    written_sha256: str
    wrote_new_raw: bool
    prior_manifest_bytes: bytes | None


def _merge_aliases(existing: Sequence[str], new: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    return [a for a in (*existing, *new) if a not in seen and not seen.add(a)]  # type: ignore[func-returns-value]


def write_raw_transcript(
    *,
    files_root: Path,
    source: str,
    date_str: str,
    native_id: str,
    native_ext: str,
    raw_bytes: bytes,
    exporter: str,
    exporter_version: str = "1.0",
    aliases: Sequence[str] = (),
    captured_at: str | None = None,
    status: RawTranscriptStatus = "complete",
    expected_sha256: str | None = None,
) -> WriteRawTranscriptResult:
    """Write raw bytes once per distinct payload; supersede prior revisions when bytes change."""
    actual_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    if expected_sha256 is not None and actual_sha256 != expected_sha256:
        raise RawTranscriptDigestMismatchError(
            expected_sha256=expected_sha256, actual_sha256=actual_sha256
        )
    ext = _normalize_ext(native_ext)
    captured = captured_at or datetime.now(UTC).isoformat()
    prev_manifest_rel: str | None = None
    revision = 2
    while True:
        stem = native_id if prev_manifest_rel is None else f"{native_id}__r{revision}"
        raw_rel = raw_transcript_rel_path(
            source=source, date_str=date_str, native_id=stem, native_ext=ext
        )
        manifest_rel = raw_manifest_rel_path(source=source, date_str=date_str, native_id=stem)
        raw_abs, manifest_abs = files_root / raw_rel, files_root / manifest_rel
        if not raw_abs.is_file():
            manifest = RawTranscriptManifest(
                source, exporter, exporter_version, native_id, raw_rel, list(aliases),
                captured, len(raw_bytes), actual_sha256, status, prev_manifest_rel,
            )
            written_sha256 = durable_write_bytes(raw_abs, raw_bytes, retain_store_root=files_root)
            durable_write_text(manifest_abs, manifest.to_json(), retain_store_root=files_root)
            return WriteRawTranscriptResult(
                raw_rel, manifest_rel, raw_abs, manifest_abs, manifest,
                written_sha256, True, None,
            )
        if hashlib.sha256(raw_abs.read_bytes()).hexdigest() != actual_sha256:
            prev_manifest_rel = manifest_rel
            if stem != native_id:
                revision += 1
            continue
        prior_bytes = manifest_abs.read_bytes() if manifest_abs.is_file() else None
        prior: RawTranscriptManifest | None = None
        if prior_bytes:
            try:
                prior = RawTranscriptManifest.from_dict(json.loads(prior_bytes.decode()))
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                pass
        manifest = RawTranscriptManifest(
            source, exporter, exporter_version, native_id, raw_rel,
            _merge_aliases(prior.aliases, aliases) if prior else list(aliases),
            prior.captured_at if prior else captured, len(raw_bytes), actual_sha256, status,
            prior.supersedes if prior else None,
        )
        durable_write_text(manifest_abs, manifest.to_json(), retain_store_root=files_root)
        return WriteRawTranscriptResult(
            raw_rel, manifest_rel, raw_abs, manifest_abs, manifest, actual_sha256, False, prior_bytes
        )


def restore_raw_transcript(result: WriteRawTranscriptResult, *, files_root: Path) -> None:
    """Undo ``write_raw_transcript`` using paths and flags from its result."""
    if result.wrote_new_raw:
        result.raw_abs_path.unlink(missing_ok=True)
        result.manifest_abs_path.unlink(missing_ok=True)
        return
    if result.prior_manifest_bytes is not None:
        durable_write_text(
            result.manifest_abs_path,
            result.prior_manifest_bytes.decode("utf-8"),
            retain_store_root=files_root,
        )
        return
    result.manifest_abs_path.unlink(missing_ok=True)
