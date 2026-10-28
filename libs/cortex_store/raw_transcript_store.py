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

from durable_io import PreImageMismatchError, durable_write_bytes, durable_write_text

ALLOWED_RAW_SOURCES: tuple[str, ...] = ("claude-ai", "cursor", "grok-bot")
ALLOWED_TRANSCRIPT_KINDS: tuple[str, ...] = ("raw", "messages")
DEFAULT_NOHOUSE = "_nohouse"
RawTranscriptSource = Literal["claude-ai", "cursor", "grok-bot"]
RawTranscriptStatus = Literal["complete", "partial"]
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_EXT_RE = re.compile(r"^[A-Za-z0-9]+$")
_HOUSE_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_NATIVE_ID_REVISION_RE = re.compile(r"__r\d+$")
_MANIFEST_KEYS = ("sha256", "bytes", "captured_at", "house", "source", "kind", "native_id", "raw_rel_path", "exporter", "exporter_version", "status", "aliases", "chat_url", "registration_id", "route", "supersedes")


def _resolve_house(house: str | None) -> str:
    if house is None or not str(house).strip():
        return DEFAULT_NOHOUSE
    resolved = str(house).strip()
    if not _HOUSE_RE.match(resolved):
        raise ValueError(f"invalid house: {house!r}")
    return resolved


def _validate_common(
    source: str,
    date_str: str,
    native_id: str,
    *,
    kind: str = "raw",
    file_stem: bool = False,
) -> None:
    if source not in ALLOWED_RAW_SOURCES:
        raise ValueError(f"invalid raw transcript source: {source!r}")
    if kind not in ALLOWED_TRANSCRIPT_KINDS:
        raise ValueError(f"invalid transcript kind: {kind!r}")
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
    if not file_stem and (
        _NATIVE_ID_REVISION_RE.search(native_id) or native_id.lower().endswith(".manifest")
    ):
        raise ValueError(f"invalid native_id: {native_id!r}")


def _normalize_ext(native_ext: str) -> str:
    ext = native_ext.lstrip(".")
    if not ext or not _EXT_RE.match(ext):
        raise ValueError(f"invalid native_ext: {native_ext!r}")
    return ext


def _transcript_base(
    *, house: str | None, source: str, kind: str, date_str: str, native_id: str, file_stem: bool
) -> str:
    h = _resolve_house(house)
    _validate_common(source, date_str, native_id, kind=kind, file_stem=file_stem)
    return f"notes/system/transcripts/{h}/{source}/{kind}/{date_str}/{native_id}"


def transcript_artifact_rel_path(
    *, house: str | None = None, source: str, kind: str = "raw", date_str: str, native_id: str,
    ext: str, file_stem: bool = False,
) -> str:
    return f"{_transcript_base(house=house, source=source, kind=kind, date_str=date_str, native_id=native_id, file_stem=file_stem)}.{_normalize_ext(ext)}"


def raw_transcript_rel_path(
    *,
    source: str,
    date_str: str,
    native_id: str,
    native_ext: str,
    house: str | None = None,
    kind: str = "raw",
    file_stem: bool = False,
) -> str:
    return transcript_artifact_rel_path(
        house=house,
        source=source,
        kind=kind,
        date_str=date_str,
        native_id=native_id,
        ext=native_ext,
        file_stem=file_stem,
    )


def raw_manifest_rel_path(
    *, source: str, date_str: str, native_id: str, house: str | None = None, kind: str = "raw",
    file_stem: bool = False,
) -> str:
    return f"{_transcript_base(house=house, source=source, kind=kind, date_str=date_str, native_id=native_id, file_stem=file_stem)}.manifest.json"


@dataclass(frozen=True)
class RawTranscriptManifest:
    sha256: str
    bytes: int
    captured_at: str
    house: str
    source: str
    native_id: str
    raw_rel_path: str
    aliases: list[str]
    kind: str = "raw"
    exporter: str = ""
    exporter_version: str = "1.0"
    status: str = "complete"
    chat_url: str | None = None
    registration_id: str | None = None
    route: str | None = None
    supersedes: str | None = None

    @property
    def surface(self) -> str: return self.source

    def to_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in _MANIFEST_KEYS}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n"

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RawTranscriptManifest:
        if (src := data.get("source", data.get("surface"))) is None:
            raise KeyError("source")
        sup = data.get("supersedes")
        return cls(
            str(data["sha256"]), int(data["bytes"]), str(data["captured_at"]),
            str(data["house"]), str(src), str(data["native_id"]), str(data["raw_rel_path"]),
            [str(a) for a in data["aliases"]], str(data.get("kind", "raw")),
            str(data.get("exporter", "")), str(data.get("exporter_version", "1.0")),
            str(data.get("status", "complete")),
            None if data.get("chat_url") is None else str(data["chat_url"]),
            None if data.get("registration_id") is None else str(data["registration_id"]),
            None if data.get("route") is None else str(data["route"]),
            None if sup is None else str(sup),
        )


class RawTranscriptDigestMismatchError(ValueError):
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


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _merge(existing: Sequence[str], new: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    return [a for a in (*existing, *new) if a not in seen and not seen.add(a)]  # type: ignore[func-returns-value]


def _idempotent_reconcile(**kw: Any) -> WriteRawTranscriptResult:
    manifest_abs: Path = kw["manifest_abs"]
    prior_bytes = manifest_abs.read_bytes() if manifest_abs.is_file() else None
    prior: RawTranscriptManifest | None = None
    if prior_bytes:
        try:
            prior = RawTranscriptManifest.from_dict(json.loads(prior_bytes.decode()))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            pass
    manifest = RawTranscriptManifest(
        kw["actual_sha256"], len(kw["raw_bytes"]), prior.captured_at if prior else kw["captured"],
        kw["resolved_house"], kw["source"], kw["native_id"], kw["raw_rel"],
        _merge(prior.aliases, kw["aliases"]) if prior else list(kw["aliases"]),
        kw["kind"], kw["exporter"], kw["exporter_version"], kw["status"],
        kw["chat_url"], kw["registration_id"], kw["route"],
        prior.supersedes if prior else kw["prev_manifest_rel"],
    )
    durable_write_text(kw["manifest_abs"], manifest.to_json(), retain_store_root=kw["files_root"])
    return WriteRawTranscriptResult(
        kw["raw_rel"], kw["manifest_rel"], kw["raw_abs"], kw["manifest_abs"],
        manifest, kw["actual_sha256"], False, prior_bytes,
    )


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
    house: str | None = None,
    kind: str = "raw",
    chat_url: str | None = None,
    registration_id: str | None = None,
    route: str | None = None,
) -> WriteRawTranscriptResult:
    _validate_common(source, date_str, native_id, kind=kind)
    actual_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    if expected_sha256 is not None and actual_sha256 != expected_sha256:
        raise RawTranscriptDigestMismatchError(
            expected_sha256=expected_sha256, actual_sha256=actual_sha256
        )
    ext = _normalize_ext(native_ext)
    captured = captured_at or datetime.now(UTC).isoformat()
    resolved_house = _resolve_house(house)
    prev_manifest_rel: str | None = None
    revision = 2
    ctx: dict[str, Any] = dict(
        files_root=files_root, resolved_house=resolved_house, source=source, kind=kind, native_id=native_id,
        exporter=exporter, exporter_version=exporter_version, aliases=aliases, captured=captured,
        raw_bytes=raw_bytes, actual_sha256=actual_sha256, status=status, chat_url=chat_url,
        registration_id=registration_id, route=route,
    )
    while True:
        stem = native_id if prev_manifest_rel is None else f"{native_id}__r{revision}"
        stem_kw = dict(
            file_stem=True, house=house, source=source, date_str=date_str, native_id=stem, kind=kind
        )
        raw_rel = raw_transcript_rel_path(**stem_kw, native_ext=ext)
        manifest_rel = raw_manifest_rel_path(**stem_kw)
        raw_abs, manifest_abs = files_root / raw_rel, files_root / manifest_rel

        if not raw_abs.is_file():
            manifest = RawTranscriptManifest(
                actual_sha256, len(raw_bytes), captured, resolved_house, source, native_id,
                raw_rel, list(aliases), kind, exporter, exporter_version, status,
                chat_url, registration_id, route, prev_manifest_rel,
            )
            try:
                written_sha256 = durable_write_bytes(
                    raw_abs, raw_bytes, expected_pre_image="", retain_store_root=files_root
                )
            except PreImageMismatchError:
                if _sha256_file(raw_abs) == actual_sha256:
                    return _idempotent_reconcile(
                        **ctx, manifest_abs=manifest_abs, raw_rel=raw_rel, manifest_rel=manifest_rel,
                        raw_abs=raw_abs, prev_manifest_rel=prev_manifest_rel,
                    )
                prev_manifest_rel = manifest_rel
                if stem != native_id:
                    revision += 1
                continue
            durable_write_text(manifest_abs, manifest.to_json(), retain_store_root=files_root)
            return WriteRawTranscriptResult(
                raw_rel, manifest_rel, raw_abs, manifest_abs, manifest, written_sha256, True, None
            )

        if _sha256_file(raw_abs) != actual_sha256:
            prev_manifest_rel = manifest_rel
            if stem != native_id:
                revision += 1
            continue
        return _idempotent_reconcile(
            **ctx, manifest_abs=manifest_abs, raw_rel=raw_rel, manifest_rel=manifest_rel,
            raw_abs=raw_abs, prev_manifest_rel=prev_manifest_rel,
        )


def restore_raw_transcript(result: WriteRawTranscriptResult, *, files_root: Path) -> None:
    if result.wrote_new_raw:
        result.raw_abs_path.unlink(missing_ok=True)
        result.manifest_abs_path.unlink(missing_ok=True)
    elif result.prior_manifest_bytes is not None:
        durable_write_text(
            result.manifest_abs_path, result.prior_manifest_bytes.decode("utf-8"),
            retain_store_root=files_root,
        )
    else:
        result.manifest_abs_path.unlink(missing_ok=True)
