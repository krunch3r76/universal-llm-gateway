"""Resolve sink URIs and write .eml files with content-hash dedup."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from implement_admission.closeout_helpers import cortex_files_root
from implement_admission.scheme_resolve import parse_schemed_path

from email_export.receipt import ReceiptStatus


@dataclass(frozen=True, slots=True)
class SinkWriteResult:
    status: ReceiptStatus
    sink_path: Path
    content_hash: str


def content_hash(mime_bytes: bytes) -> str:
    return hashlib.sha256(mime_bytes).hexdigest()


def resolve_sink_dir(sink_uri: str, *, override: Path | None = None) -> Path:
    """Map cortex://notes/... to a filesystem directory."""
    if override is not None:
        return override.expanduser().resolve()
    parsed = parse_schemed_path(sink_uri)
    if parsed.scheme != "cortex":
        raise ValueError(
            f"sink_uri must use cortex:// scheme for v0, got {sink_uri!r}"
        )
    rel = parsed.rel_path.strip("/")
    if not rel:
        raise ValueError(f"sink_uri missing path: {sink_uri!r}")
    root = cortex_files_root().resolve()
    target = (root / rel).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"sink_uri escapes cortex root: {sink_uri!r}") from exc
    return target


def write_eml(sink_dir: Path, mime_bytes: bytes) -> SinkWriteResult:
    """Write MIME bytes as {content_hash}.eml; dedup when hash already present."""
    digest = content_hash(mime_bytes)
    target = sink_dir / f"{digest}.eml"
    if target.exists():
        existing = target.read_bytes()
        if existing == mime_bytes:
            return SinkWriteResult(
                status="already_present", sink_path=target, content_hash=digest
            )
        return SinkWriteResult(
            status="deduped", sink_path=target, content_hash=digest
        )
    sink_dir.mkdir(parents=True, exist_ok=True)
    target.write_bytes(mime_bytes)
    return SinkWriteResult(status="fetched", sink_path=target, content_hash=digest)
