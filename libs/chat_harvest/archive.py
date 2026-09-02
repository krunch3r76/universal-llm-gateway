"""Persist full chat transcripts under CORTEX_FILES_ROOT."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from durable_io.atomic import durable_write_text
from universal_logging import get_logger

from chat_harvest.models import ChatTurn, ConflictDetail

logger = get_logger(__name__)

_TURN_HEADING_RE = re.compile(r"^## Turn (\d+) — (user|assistant)\s*$", re.MULTILINE)
_INDEX_RE = re.compile(
    r"<!-- chat-harvest-index (\{.*?\}) -->",
    re.DOTALL,
)
_CONV12_RE = re.compile(r"[^a-z0-9-]")
_SNIPPET_MAX = 200


class Alignment(StrEnum):
    IDENTICAL = "identical"
    EXTENSION = "extension"
    WINDOW = "window"
    HEAD_EXTENSION = "head_extension"
    DIVERGENT = "divergent"


class ArchiveConflictError(Exception):
    """Existing archive diverges from the new harvest at a shared turn."""

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
            f"archive conflict at {path} turn {detail.ordinal}: "
            f"existing digest {detail.existing_digest!r} vs new {detail.new_digest!r}"
        )


class ArchiveRefusalError(Exception):
    """Archive write refused (unindexed legacy file or narrower capture)."""

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


def archive_rel_path(
    site: str,
    conversation_id: str,
    *,
    version: int | None = None,
) -> str:
    base = _base_name(site, conversation_id)
    if version is not None and version > 1:
        return f"notes/system/threads/{base}-v{version}.md"
    return f"notes/system/threads/{base}.md"


def archive_dest(
    site: str,
    conversation_id: str,
    *,
    version: int | None = None,
) -> Path:
    return cortex_files_root() / archive_rel_path(
        site, conversation_id, version=version
    )


def normalize_turn_body(text: str) -> str:
    """Normalize turn body text for stable digest comparison."""
    return text.strip().replace("\r\n", "\n")


def turn_digest(text: str) -> str:
    """SHA-256 hex digest of normalized turn body."""
    normalized = normalize_turn_body(text)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def build_turn_index(turns: list[ChatTurn]) -> list[list[object]]:
    """Build index rows ``[ordinal, author, digest]`` for each turn."""
    return [[t.ordinal, t.author, turn_digest(t.text)] for t in turns]


def parse_index(content: str) -> list[list[object]] | None:
    """Parse ``<!-- chat-harvest-index ... -->`` from archive content."""
    match = _INDEX_RE.search(content)
    if not match:
        return None
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    turns = payload.get("turns")
    if not isinstance(turns, list):
        return None
    return turns


def _parse_turn_bodies(content: str) -> dict[int, tuple[str, str]]:
    """Return ``ordinal -> (author, body)`` parsed from archive turn headings."""
    matches = list(_TURN_HEADING_RE.finditer(content))
    bodies: dict[int, tuple[str, str]] = {}
    for idx, match in enumerate(matches):
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(content)
        body = content[start:end].strip("\n")
        bodies[int(match.group(1))] = (match.group(2), body)
    return bodies


def _snippet(text: str) -> str:
    text = normalize_turn_body(text)
    if len(text) <= _SNIPPET_MAX:
        return text
    return text[: _SNIPPET_MAX - 3] + "..."


def align_transcripts(
    existing_index: list[list[object]],
    turns: list[ChatTurn],
) -> Alignment:
    """Compare an existing archive index against a new harvest."""
    new_index = build_turn_index(turns)

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

    return Alignment.DIVERGENT


def _conflict_detail(
    existing_index: list[list[object]],
    turns: list[ChatTurn],
    existing_content: str,
) -> ConflictDetail:
    """Build conflict detail for the first divergent ordinal."""
    new_index = build_turn_index(turns)
    existing_bodies = _parse_turn_bodies(existing_content)
    turn_by_ordinal = {t.ordinal: t for t in turns}

    min_len = min(len(existing_index), len(new_index))
    for i in range(min_len):
        eo, _ea, ed = existing_index[i]
        no, _na, nd = new_index[i]
        if [eo, _ea, ed] != [no, _na, nd]:
            ordinal = int(eo)
            existing_text = existing_bodies.get(ordinal, ("", ""))[1]
            new_turn = turn_by_ordinal.get(int(no)) or turn_by_ordinal.get(ordinal)
            new_text = new_turn.text if new_turn else ""
            return ConflictDetail(
                ordinal=ordinal,
                existing_digest=str(ed),
                new_digest=str(nd),
                existing_len=len(existing_text),
                new_len=len(new_text),
                existing_snippet=_snippet(existing_text),
                new_snippet=_snippet(new_text),
            )

    if len(new_index) > len(existing_index):
        no, _na, nd = new_index[len(existing_index)]
        ordinal = int(no)
        new_turn = turn_by_ordinal.get(ordinal)
        new_text = new_turn.text if new_turn else ""
        return ConflictDetail(
            ordinal=ordinal,
            existing_digest="",
            new_digest=str(nd),
            existing_len=0,
            new_len=len(new_text),
            existing_snippet="",
            new_snippet=_snippet(new_text),
        )

    eo, _ea, ed = existing_index[len(new_index)]
    ordinal = int(eo)
    existing_text = existing_bodies.get(ordinal, ("", ""))[1]
    return ConflictDetail(
        ordinal=ordinal,
        existing_digest=str(ed),
        new_digest="",
        existing_len=len(existing_text),
        new_len=0,
        existing_snippet=_snippet(existing_text),
        new_snippet="",
    )


def _renumber_turns(turns: list[ChatTurn]) -> list[ChatTurn]:
    return [
        ChatTurn(author=t.author, ordinal=i + 1, text=t.text, source=t.source)
        for i, t in enumerate(turns)
    ]


def _index_comment(turns: list[ChatTurn]) -> str:
    payload = {"turns": build_turn_index(turns)}
    return f"<!-- chat-harvest-index {json.dumps(payload, separators=(',', ':'))} -->"


def _format_transcript(
    *,
    site: str,
    conversation_id: str,
    url: str,
    turns: list[ChatTurn],
    harvested_at: str,
    streaming: bool,
) -> str:
    lines = [
        f"# Chat harvest — {site}",
        "",
        f"- site: `{site}`",
        f"- conversation_id: `{conversation_id}`",
        f"- url: `{url}`",
        f"- harvested_at: `{harvested_at}`",
        f"- turn_count: `{len(turns)}`",
        f"- streaming_at_harvest: `{str(streaming).lower()}`",
        "",
        _index_comment(turns),
        "",
    ]
    for turn in turns:
        lines.append(f"## Turn {turn.ordinal} — {turn.author}")
        lines.append(turn.text)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


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
    for path in threads.glob(f"{base}-v*.md"):
        match = re.search(r"-v(\d+)\.md$", path.name)
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
) -> tuple[str, str]:
    """Write a full transcript sidecar; return ``(cortex_uri, sha256)``."""
    if not conversation_id:
        raise ValueError("conversation_id must be non-empty to archive")

    dest = archive_dest(site, conversation_id)
    write_turns = list(turns)

    if dest.is_file() and supersede:
        version = _next_supersede_version(site, conversation_id)
        versioned = archive_dest(site, conversation_id, version=version)
        versioned.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(dest, versioned)
    elif dest.is_file() and not supersede:
        existing_content = dest.read_text(encoding="utf-8")
        existing_index = parse_index(existing_content)
        if existing_index is None:
            raise ArchiveRefusalError(
                path=dest,
                code="archive_unindexed",
                reason="existing archive lacks chat-harvest-index header",
            )

        alignment = align_transcripts(existing_index, write_turns)

        if alignment == Alignment.IDENTICAL:
            uri = _cortex_uri(dest)
            sha256 = _sha256_of_file(dest)
            logger.info(
                "archive identical — skip rewrite site=%s conversation_id=%s uri=%s",
                site,
                conversation_id,
                uri,
            )
            return uri, sha256

        if alignment == Alignment.WINDOW:
            raise ArchiveRefusalError(
                path=dest,
                code="narrower_capture",
                reason="new harvest is a narrower window than the archived transcript",
            )

        if alignment == Alignment.DIVERGENT:
            detail = _conflict_detail(existing_index, write_turns, existing_content)
            raise ArchiveConflictError(
                path=dest,
                existing_sha256=_sha256_of_file(dest),
                detail=detail,
            )

        if alignment == Alignment.HEAD_EXTENSION:
            write_turns = _renumber_turns(write_turns)

    when = harvested_at or datetime.now(UTC).isoformat()
    content = _format_transcript(
        site=site,
        conversation_id=conversation_id,
        url=url,
        turns=write_turns,
        harvested_at=when,
        streaming=streaming,
    )

    dest.parent.mkdir(parents=True, exist_ok=True)
    sha256 = durable_write_text(dest, content)
    uri = _cortex_uri(dest)
    logger.info(
        "archived chat transcript site=%s conversation_id=%s uri=%s sha256=%s",
        site,
        conversation_id,
        uri,
        sha256,
    )
    return uri, sha256
