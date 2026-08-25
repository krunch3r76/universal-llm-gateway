"""Persist full chat transcripts under CORTEX_FILES_ROOT."""

from __future__ import annotations

import hashlib
import os
import re
from datetime import UTC, datetime
from pathlib import Path

from durable_io.atomic import durable_write_text
from universal_logging import get_logger

from chat_harvest.models import ChatTurn

logger = get_logger(__name__)

_TURN_HEADING_RE = re.compile(r"^## Turn (\d+) — (user|assistant)\s*$", re.MULTILINE)
_CONV12_RE = re.compile(r"[^a-z0-9-]")


class ArchiveConflictError(Exception):
    """Existing archive is not a prefix-superset of the new harvest."""

    def __init__(self, *, path: Path, existing_sha256: str) -> None:
        self.path = path
        self.existing_sha256 = existing_sha256
        super().__init__(
            f"archive conflict at {path}: existing sha256 {existing_sha256!r}"
        )


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
    ]
    for turn in turns:
        lines.append(f"## Turn {turn.ordinal} — {turn.author}")
        lines.append(turn.text)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def parse_turns_from_archive(content: str) -> list[tuple[int, str, str]]:
    """Return (ordinal, author, text) tuples parsed from an archive body."""
    matches = list(_TURN_HEADING_RE.finditer(content))
    if not matches:
        return []
    turns: list[tuple[int, str, str]] = []
    for idx, match in enumerate(matches):
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(content)
        body = content[start:end].strip("\n")
        turns.append((int(match.group(1)), match.group(2), body))
    return turns


def is_prefix_superset(
    existing: list[tuple[int, str, str]],
    new: list[tuple[int, str, str]],
) -> bool:
    """True when existing turns 1..K match new turns 1..K and new has >= K turns."""
    if not existing:
        return True
    if len(new) < len(existing):
        return False
    for old, fresh in zip(existing, new, strict=False):
        if old != fresh:
            return False
    return len(new) >= len(existing)


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

    when = harvested_at or datetime.now(UTC).isoformat()
    content = _format_transcript(
        site=site,
        conversation_id=conversation_id,
        url=url,
        turns=turns,
        harvested_at=when,
        streaming=streaming,
    )
    dest = archive_dest(site, conversation_id)
    new_turns = [(t.ordinal, t.author, t.text) for t in turns]

    if dest.is_file() and not supersede:
        existing_turns = parse_turns_from_archive(dest.read_text(encoding="utf-8"))
        if not is_prefix_superset(existing_turns, new_turns):
            raise ArchiveConflictError(
                path=dest,
                existing_sha256=_sha256_of_file(dest),
            )

    write_dest = dest
    if supersede and dest.is_file():
        version = _next_supersede_version(site, conversation_id)
        write_dest = archive_dest(site, conversation_id, version=version)

    write_dest.parent.mkdir(parents=True, exist_ok=True)
    sha256 = durable_write_text(write_dest, content)
    rel = write_dest.relative_to(cortex_files_root()).as_posix()
    uri = f"cortex://{rel}"
    logger.info(
        "archived chat transcript site=%s conversation_id=%s uri=%s sha256=%s",
        site,
        conversation_id,
        uri,
        sha256,
    )
    return uri, sha256
