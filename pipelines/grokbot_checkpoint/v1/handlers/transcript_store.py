"""Canonical grok-bot transcript store writes after harvest_seal checkpoint pour."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cortex_store.grok_raw_transcript_export import export_grok_read_transcript_partial
from durable_io.atomic import durable_write_text

logger = logging.getLogger(__name__)

_SOURCE = "grok-bot"
_MESSAGES_KIND = "messages"
_REVISION_RE = re.compile(r"__r(\d+)$")


def resolve_transcript_house(options: dict[str, Any]) -> str | None:
    """House for canonical store; None → ``_nohouse`` in write_raw_transcript."""
    houseless = options.get("houseless") is True
    house = str(options.get("house") or "").strip()
    if houseless or not house or house.lower() == "none":
        return None
    token = house.removeprefix("agent-bus:").strip()
    return token or None


def _revision_from_raw_rel(raw_rel_path: str, native_id: str) -> int | None:
    stem = Path(raw_rel_path).name.rsplit(".", 1)[0]
    if stem == native_id:
        return None
    match = _REVISION_RE.search(stem)
    if match and stem.startswith(native_id):
        return int(match.group(1))
    return None


def _prev_messages_manifest_rel(
    *,
    files_root: Path,
    house: str,
    date_str: str,
    native_id: str,
    revision: int | None,
) -> str | None:
    if revision is None or revision < 2:
        return None
    prev_rev = revision - 1 if revision > 2 else None
    if prev_rev is None:
        stem = native_id
    else:
        stem = f"{native_id}__r{prev_rev}"
    rel = (
        f"notes/system/transcripts/{house}/{_SOURCE}/{_MESSAGES_KIND}/"
        f"{date_str}/{stem}.manifest.json"
    )
    return rel if (files_root / rel).is_file() else None


def _persist_grok_messages_projection(
    *,
    files_root: Path,
    house: str,
    native_id: str,
    date_str: str,
    raw_envelope: dict[str, Any],
    raw_sha256: str,
    raw_rel_path: str,
    captured_at: str,
    revision: int | None,
    prev_messages_manifest_rel: str | None,
) -> tuple[str, str, str]:
    messages_dir = (
        files_root
        / "notes/system/transcripts"
        / house
        / _SOURCE
        / _MESSAGES_KIND
        / date_str
    )
    messages_dir.mkdir(parents=True, exist_ok=True)
    if revision is None:
        messages_abs = messages_dir / f"{native_id}.messages.json"
        messages_manifest_abs = messages_dir / f"{native_id}.manifest.json"
    else:
        messages_abs = messages_dir / f"{native_id}__r{revision}.messages.json"
        messages_manifest_abs = messages_dir / f"{native_id}__r{revision}.manifest.json"

    projection = json.loads(json.dumps(raw_envelope, sort_keys=True))
    meta = projection.setdefault("meta", {})
    if isinstance(meta, dict):
        meta["raw_sha256"] = raw_sha256
    projection["raw_sha256"] = raw_sha256
    messages_text = json.dumps(projection, indent=2, ensure_ascii=False) + "\n"
    messages_bytes = messages_text.encode("utf-8")
    messages_sha256 = hashlib.sha256(messages_bytes).hexdigest()
    durable_write_text(messages_abs, messages_text, retain_store_root=files_root)

    messages_supersedes: str | None = None
    if revision is not None and prev_messages_manifest_rel:
        messages_supersedes = prev_messages_manifest_rel

    messages_manifest: dict[str, Any] = {
        "sha256": messages_sha256,
        "bytes": len(messages_bytes),
        "captured_at": captured_at,
        "house": house,
        "source": _SOURCE,
        "kind": _MESSAGES_KIND,
        "native_id": native_id,
        "raw_sha256": raw_sha256,
        "raw_rel_path": raw_rel_path,
        "supersedes": messages_supersedes,
    }
    durable_write_text(
        messages_manifest_abs,
        json.dumps(messages_manifest, indent=2, ensure_ascii=False) + "\n",
        retain_store_root=files_root,
    )
    messages_rel = messages_abs.relative_to(files_root).as_posix()
    manifest_rel = messages_manifest_abs.relative_to(files_root).as_posix()
    return messages_rel, manifest_rel, messages_sha256


def persist_grok_checkpoint_transcript(
    *,
    files_root: Path,
    options: dict[str, Any],
    raw_bytes: bytes,
    raw_envelope: dict[str, Any],
    l0_ref: str,
) -> dict[str, Any]:
    """Write canonical raw + messages under notes/system/transcripts (source=grok-bot)."""
    agent_id = str(options.get("agent_id") or "").strip()
    n = int(options.get("n") or 1)
    date_str = datetime.now(UTC).strftime("%Y-%m-%d")
    captured_at = datetime.now(UTC).isoformat()
    house_opt = resolve_transcript_house(options)
    aliases: list[str] = [
        f"grok-agent:{agent_id}",
        f"checkpoint:grok/{agent_id}/{n}",
        l0_ref,
    ]
    dispatch_thread = str(options.get("dispatch_thread_id") or "").strip()
    if dispatch_thread:
        aliases.append(f"agent-bus:{dispatch_thread}")

    raw_export = export_grok_read_transcript_partial(
        files_root=files_root,
        native_id=agent_id,
        payload_bytes=raw_bytes,
        date_str=date_str,
        house=house_opt,
        native_ext="txt",
        aliases=aliases,
        captured_at=captured_at,
    )
    resolved_house = raw_export["manifest"]["house"]
    raw_rel_path = str(raw_export["raw_rel_path"])
    raw_manifest_rel_path = str(raw_export["manifest_rel_path"])
    raw_sha256 = str(raw_export["manifest"]["sha256"])
    revision = _revision_from_raw_rel(raw_rel_path, agent_id)
    prev_msg_manifest = _prev_messages_manifest_rel(
        files_root=files_root,
        house=resolved_house,
        date_str=date_str,
        native_id=agent_id,
        revision=revision,
    )
    messages_rel, messages_manifest_rel, messages_sha256 = (
        _persist_grok_messages_projection(
            files_root=files_root,
            house=resolved_house,
            native_id=agent_id,
            date_str=date_str,
            raw_envelope=raw_envelope,
            raw_sha256=raw_sha256,
            raw_rel_path=raw_rel_path,
            captured_at=captured_at,
            revision=revision,
            prev_messages_manifest_rel=prev_msg_manifest,
        )
    )
    return {
        "status": raw_export["status"],
        "house": resolved_house,
        "native_id": agent_id,
        "raw_rel_path": raw_rel_path,
        "raw_manifest_rel_path": raw_manifest_rel_path,
        "raw_sha256": raw_sha256,
        "messages_rel_path": messages_rel,
        "messages_manifest_rel_path": messages_manifest_rel,
        "messages_sha256": messages_sha256,
    }


async def maybe_persist_canonical_transcript(
    fs: Any,
    *,
    options: dict[str, Any],
    raw_bytes: bytes,
    raw_envelope: dict[str, Any],
    l0_ref: str,
) -> dict[str, Any]:
    """Skip when fs has no files_root (MemFs tests); never raises to caller."""
    root = getattr(fs, "files_root", None)
    if root is None:
        return {"status": "skipped"}
    files_root = Path(root).resolve()
    try:
        return persist_grok_checkpoint_transcript(
            files_root=files_root,
            options=options,
            raw_bytes=raw_bytes,
            raw_envelope=raw_envelope,
            l0_ref=l0_ref,
        )
    except Exception as exc:
        logger.warning("grok checkpoint canonical transcript write failed: %s", exc)
        return {"status": "error", "error": str(exc)}


__all__ = [
    "maybe_persist_canonical_transcript",
    "persist_grok_checkpoint_transcript",
    "resolve_transcript_house",
]
