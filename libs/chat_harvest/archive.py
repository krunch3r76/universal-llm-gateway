"""Persist chat harvest envelopes under CORTEX_FILES_ROOT."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from durable_io.atomic import durable_write_text
from universal_logging import get_logger

from chat_harvest.messages import (
    build_harvest_envelope,
    envelope_json_text,
    load_harvest_envelope,
    message_digest,
    message_index,
    turns_to_messages,
)
from chat_harvest.models import ChatTurn, ConflictDetail

logger = get_logger(__name__)

_TURN_HEADING_RE = re.compile(r"^## Turn (\d+) — (user|assistant)\s*$", re.MULTILINE)
_CONV12_RE = re.compile(r"[^a-z0-9-]")
_SNIPPET_MAX = 200


class Alignment(StrEnum):
    IDENTICAL = "identical"
    EXTENSION = "extension"
    WINDOW = "window"
    HEAD_EXTENSION = "head_extension"
    WINDOW_SLIDE = "window_slide"
    DIVERGENT = "divergent"
    FIRST_WRITE = "first_write"


class ArchiveConflictError(Exception):
    """Existing archive diverges from the new harvest at a shared message."""

    def __init__(
        self,
        *,
        path: Path,
        existing_sha256: str,
        detail: ConflictDetail,
    ) -> None:
        self.path = path
        self.existing_sha256 = existing_sha256
        self.detail = detail
        super().__init__(
            f"archive conflict at {path} message {detail.ordinal}: "
            f"existing digest {detail.existing_digest!r} vs new {detail.new_digest!r}"
        )


class ArchiveRefusalError(Exception):
    """Archive write refused (narrower capture or window shift)."""

    def __init__(self, *, path: Path, code: str, reason: str) -> None:
        self.path = path
        self.code = code
        self.reason = reason
        super().__init__(f"{code} at {path}: {reason}")


def cortex_files_root() -> Path:
    return Path(os.environ.get("CORTEX_FILES_ROOT", "/mnt/torus/mcp-data/files"))


def conv12(conversation_id: str) -> str:
    """First 12 chars of conversation_id, lowercase, [a-z0-9-] only."""
    cleaned = _CONV12_RE.sub("", conversation_id.lower())
    return cleaned[:12]


def _base_name(site: str, conversation_id: str) -> str:
    return f"chat-harvest-{site}-{conv12(conversation_id)}"


def legacy_md_rel_path(site: str, conversation_id: str) -> str:
    base = _base_name(site, conversation_id)
    return f"notes/system/threads/{base}.md"


def archive_rel_path(
    site: str,
    conversation_id: str,
    *,
    version: int | None = None,
) -> str:
    base = _base_name(site, conversation_id)
    if version is not None and version > 1:
        return f"notes/system/threads/{base}-v{version}.messages.json"
    return f"notes/system/threads/{base}.messages.json"


def archive_dest(
    site: str,
    conversation_id: str,
    *,
    version: int | None = None,
) -> Path:
    return cortex_files_root() / archive_rel_path(
        site, conversation_id, version=version
    )


def legacy_md_dest(site: str, conversation_id: str) -> Path:
    return cortex_files_root() / legacy_md_rel_path(site, conversation_id)


def _snippet(text: str) -> str:
    text = text.strip().replace("\r\n", "\n")
    if len(text) <= _SNIPPET_MAX:
        return text
    return text[: _SNIPPET_MAX - 3] + "..."


def align_transcripts(
    existing_index: list[list[object]],
    new_index: list[list[object]],
) -> Alignment:
    """Compare an existing message index against a freshly mapped harvest."""
    if existing_index == new_index:
        return Alignment.IDENTICAL

    if len(new_index) >= len(existing_index) and new_index[: len(existing_index)] == existing_index:
        return Alignment.EXTENSION

    if len(new_index) < len(existing_index) and new_index == existing_index[: len(new_index)]:
        return Alignment.WINDOW

    existing_body = [(row[1], row[2]) for row in existing_index]
    new_body = [(row[1], row[2]) for row in new_index]

    if len(new_body) > len(existing_body):
        for offset in range(1, len(new_body) - len(existing_body) + 1):
            if new_body[offset : offset + len(existing_body)] == existing_body:
                return Alignment.HEAD_EXTENSION

    slide = _window_slide_overlap(existing_index, new_index)
    if slide is not None:
        return Alignment.WINDOW_SLIDE

    return Alignment.DIVERGENT


def _window_slide_overlap(
    existing_index: list[list[object]],
    new_index: list[list[object]],
) -> int | None:
    """Return k>0 when existing[k:] author+digest rows match new prefix."""
    if len(existing_index) < 2 or len(new_index) < 2:
        return None

    def _body_rows(index: list[list[object]]) -> list[tuple[object, object]]:
        return [(row[1], row[2]) for row in index]

    existing_body = _body_rows(existing_index)
    new_body = _body_rows(new_index)
    for k in range(1, len(existing_body)):
        tail = existing_body[k:]
        window = new_body[: len(tail)]
        if tail and window == tail:
            return k
    return None


def _parse_turn_bodies(content: str) -> dict[int, tuple[str, str]]:
    matches = list(_TURN_HEADING_RE.finditer(content))
    bodies: dict[int, tuple[str, str]] = {}
    for idx, match in enumerate(matches):
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(content)
        body = content[start:end].strip("\n")
        bodies[int(match.group(1))] = (match.group(2), body)
    return bodies


def _legacy_messages_from_md(path: Path, *, site: str) -> list[dict]:
    content = path.read_text(encoding="utf-8")
    bodies = _parse_turn_bodies(content)
    if not bodies:
        return []
    turns: list[ChatTurn] = []
    for ordinal, (author, body) in sorted(bodies.items()):
        text = body
        if site == "claude" and author == "assistant":
            from claude_bundles.project_ask import strip_thinking_prefix

            from chat_harvest.claude_chat_adapter import _strip_claude_dom_chrome

            text = strip_thinking_prefix(_strip_claude_dom_chrome(body))
        turns.append(
            ChatTurn(author=author, ordinal=ordinal, text=text, source="archive")
        )
    return turns_to_messages(turns)


def _conflict_detail(
    existing_index: list[list[object]],
    new_index: list[list[object]],
    existing_messages: list[dict],
    new_messages: list[dict],
) -> ConflictDetail:
    min_len = min(len(existing_index), len(new_index))
    for i in range(min_len):
        if existing_index[i] != new_index[i]:
            pos = int(existing_index[i][0])
            existing_text = str(existing_messages[i].get("content") or "")
            new_text = str(new_messages[i].get("content") or "")
            return ConflictDetail(
                ordinal=pos,
                existing_digest=str(existing_index[i][2]),
                new_digest=str(new_index[i][2]),
                existing_len=len(existing_text),
                new_len=len(new_text),
                existing_snippet=_snippet(existing_text),
                new_snippet=_snippet(new_text),
            )

    if len(new_index) > len(existing_index):
        pos = int(new_index[len(existing_index)][0])
        new_text = str(new_messages[len(existing_index)].get("content") or "")
        return ConflictDetail(
            ordinal=pos,
            existing_digest="",
            new_digest=str(new_index[len(existing_index)][2]),
            existing_len=0,
            new_len=len(new_text),
            existing_snippet="",
            new_snippet=_snippet(new_text),
        )

    pos = int(existing_index[len(new_index)][0])
    existing_text = str(existing_messages[len(new_index)].get("content") or "")
    return ConflictDetail(
        ordinal=pos,
        existing_digest=str(existing_index[len(new_index)][2]),
        new_digest="",
        existing_len=len(existing_text),
        new_len=0,
        existing_snippet=_snippet(existing_text),
        new_snippet="",
    )


def _sha256_of_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _next_supersede_version(site: str, conversation_id: str) -> int:
    base = _base_name(site, conversation_id)
    threads = cortex_files_root() / "notes/system/threads"
    highest = 1
    for path in threads.glob(f"{base}-v*.messages.json"):
        match = re.search(r"-v(\d+)\.messages\.json$", path.name)
        if match:
            highest = max(highest, int(match.group(1)))
    return highest + 1


def _cortex_uri(path: Path) -> str:
    rel = path.relative_to(cortex_files_root()).as_posix()
    return f"cortex://{rel}"


def archive_chat_transcript(
    site: str,
    conversation_id: str,
    url: str,
    turns: list[ChatTurn],
    *,
    harvested_at: str | None = None,
    streaming: bool = False,
    supersede: bool = False,
) -> tuple[str, str, str]:
    """Write a messages-v1 sidecar; return ``(cortex_uri, sha256, alignment)``."""
    if not conversation_id:
        raise ValueError("conversation_id must be non-empty to archive")

    messages = turns_to_messages(turns)
    new_rows = message_index(messages)
    dest = archive_dest(site, conversation_id)
    alignment: str | None = None

    existing_rows: list[list[object]] | None = None
    existing_messages: list[dict] = []
    existing_sha = ""
    existing_source: str | None = None

    if dest.is_file():
        existing = load_harvest_envelope(dest)
        existing_messages = list(existing.messages)
        existing_rows = message_index(existing_messages)
        existing_sha = _sha256_of_file(dest)
        existing_source = "envelope"
    else:
        legacy = legacy_md_dest(site, conversation_id)
        if legacy.is_file():
            existing_messages = _legacy_messages_from_md(legacy, site=site)
            if existing_messages:
                existing_rows = message_index(existing_messages)
                existing_sha = _sha256_of_file(legacy)
                existing_source = "legacy_md"

    if dest.is_file() and supersede:
        version = _next_supersede_version(site, conversation_id)
        versioned = archive_dest(site, conversation_id, version=version)
        versioned.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(dest, versioned)
        existing_source = None
        existing_rows = None
    elif existing_rows is not None and not supersede:
        alignment = align_transcripts(existing_rows, new_rows).value

        if alignment == Alignment.IDENTICAL:
            if existing_source == "envelope":
                uri = _cortex_uri(dest)
                logger.info(
                    "archive identical — skip rewrite site=%s conversation_id=%s uri=%s",
                    site,
                    conversation_id,
                    uri,
                )
                return uri, existing_sha, alignment

        if alignment == Alignment.WINDOW:
            raise ArchiveRefusalError(
                path=dest,
                code="narrower_capture",
                reason="new harvest is a narrower window than the archived transcript",
            )

        if alignment == Alignment.WINDOW_SLIDE:
            overlap = _window_slide_overlap(existing_rows, new_rows)
            raise ArchiveRefusalError(
                path=dest,
                code="window_slide",
                reason=f"tail window slide overlap={overlap}",
            )

        if alignment == Alignment.HEAD_EXTENSION:
            raise ArchiveRefusalError(
                path=dest,
                code="head_extension",
                reason="head extension detected; write deferred until completeness capture",
            )

        if alignment == Alignment.DIVERGENT:
            detail = _conflict_detail(
                existing_rows, new_rows, existing_messages, messages
            )
            raise ArchiveConflictError(
                path=dest,
                existing_sha256=existing_sha,
                detail=detail,
            )

    when = harvested_at or datetime.now(UTC).isoformat()
    envelope = build_harvest_envelope(
        site=site,
        conversation_id=conversation_id,
        url=url,
        messages=messages,
        harvested_at=when,
        streaming=streaming,
    )
    content = envelope_json_text(envelope)

    dest.parent.mkdir(parents=True, exist_ok=True)
    sha256 = durable_write_text(dest, content)
    uri = _cortex_uri(dest)
    final_alignment = alignment or Alignment.FIRST_WRITE.value
    logger.info(
        "archived chat transcript site=%s conversation_id=%s uri=%s sha256=%s alignment=%s",
        site,
        conversation_id,
        uri,
        sha256,
        final_alignment,
    )
    return uri, sha256, final_alignment


__all__ = [
    "Alignment",
    "ArchiveConflictError",
    "ArchiveRefusalError",
    "align_transcripts",
    "archive_chat_transcript",
    "archive_dest",
    "archive_rel_path",
    "conv12",
    "cortex_files_root",
    "legacy_md_dest",
    "legacy_md_rel_path",
    "message_digest",
]
