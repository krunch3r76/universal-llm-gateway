"""Scoreboard URI resolution and family classification for CHECKPOINT tail."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from implement_admission.conductor_score_journal import scoreboard_tip_uri
from implement_admission.conductor_score_locus import charter_locus

_SCOREBOARD_LINE_RE = re.compile(
    r"^Scoreboard:\s*(.+)$",
    re.IGNORECASE | re.MULTILINE,
)
_URI_RE = re.compile(r"cortex://[^\s)\]>`'\"]+-scoreboard\.md")
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_SCOREBOARDS_PREFIX = "cortex://notes/system/scoreboards/"
_THREADS_CHARTER_SUFFIX = "-charter-scoreboard.md"


@dataclass(frozen=True, slots=True)
class ScoreboardRef:
    """Resolved scoreboard location and fold capability."""

    uri: str
    slug: str | None
    family: Literal["conductor", "charter"]
    sha256: str | None


def _files_root(files_root: Path | None) -> Path:
    if files_root is not None:
        return files_root
    import os

    root_env = os.environ.get("CORTEX_FILES_ROOT")
    if root_env:
        return Path(root_env)
    from cortex_store.dispatch_ops._shared import _FILES_ROOT

    return _FILES_ROOT


def _slug_from_uri(uri: str) -> str | None:
    if uri.startswith(_SCOREBOARDS_PREFIX) and uri.endswith("-scoreboard.md"):
        tail = uri.removeprefix(_SCOREBOARDS_PREFIX)
        return tail.removesuffix("-scoreboard.md") or None
    if "/threads/" in uri and uri.endswith(_THREADS_CHARTER_SUFFIX):
        name = uri.rsplit("/", 1)[-1]
        return name.removesuffix("-charter-scoreboard.md") or None
    return None


def _read_sha(uri: str, *, files_root: Path | None) -> str | None:
    if not uri.startswith("cortex://"):
        return None
    path = _files_root(files_root) / uri.removeprefix("cortex://")
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _family_for_uri(uri: str, *, files_root: Path | None) -> Literal["conductor", "charter"]:
    slug = _slug_from_uri(uri)
    if slug is None:
        return "charter"
    expected = scoreboard_tip_uri(slug)
    if uri != expected:
        return "charter"
    journal = _files_root(files_root) / expected.removeprefix("cortex://")
    journal_path = journal.with_name(journal.name.replace("-scoreboard.md", "-score-journal.md"))
    return "conductor" if journal_path.is_file() else "charter"


def _parse_scoreboard_line(body: str) -> str | None:
    match = _SCOREBOARD_LINE_RE.search(body or "")
    return match.group(1).strip() if match else None


def _guess_scoreboard_uri(slug: str, *, files_root: Path | None) -> str | None:
    """Return the first existing scoreboard URI for *slug*, or None when neither candidate exists."""
    root = _files_root(files_root)
    charter = charter_locus(slug, files_root=root)
    if charter.tip_path.is_file():
        return charter.tip_uri
    conductor_uri = scoreboard_tip_uri(slug)
    conductor_path = root / conductor_uri.removeprefix("cortex://")
    if conductor_path.is_file():
        return conductor_uri
    return None


def _uri_from_scoreboard_line(
    line: str,
    *,
    tags: list[str],
    files_root: Path | None,
) -> str | None:
    for token in re.split(r"\s*[·|]\s*", line):
        piece = token.strip()
        if piece.startswith("cortex://") and piece.endswith("-scoreboard.md"):
            return piece.split()[0]
        if _SLUG_RE.match(piece):
            guessed = _guess_scoreboard_uri(piece, files_root=files_root)
            if guessed is not None:
                return guessed
    for tag in tags:
        if tag.startswith("scoreboard:"):
            slug = tag.removeprefix("scoreboard:").strip()
            if slug:
                guessed = _guess_scoreboard_uri(slug, files_root=files_root)
                if guessed is not None:
                    return guessed
    return None


def _whole_body_uri(body: str) -> str | None:
    match = _URI_RE.search(body or "")
    return match.group(0) if match else None


def resolve_scoreboard_ref(
    *,
    options: dict[str, Any] | None = None,
    tip_body: str = "",
    thread_tags: list[str] | None = None,
    files_root: Path | None = None,
) -> ScoreboardRef | None:
    """Resolve scoreboard URI and family per phase-B §2.1 precedence."""
    opts = options or {}
    tags = thread_tags or []
    uri: str | None = None

    explicit = str(opts.get("scoreboard_uri") or "").strip()
    if explicit.startswith("cortex://") and explicit.endswith("-scoreboard.md"):
        uri = explicit

    if uri is None:
        line = _parse_scoreboard_line(tip_body)
        if line:
            uri = _uri_from_scoreboard_line(line, tags=tags, files_root=files_root)

    if uri is None:
        uri = _whole_body_uri(tip_body)

    if uri is None:
        for tag in tags:
            if tag.startswith("scoreboard:"):
                slug = tag.removeprefix("scoreboard:").strip()
                if slug:
                    uri = _guess_scoreboard_uri(slug, files_root=files_root)
                    if uri is not None:
                        break

    if uri is None:
        return None

    slug = _slug_from_uri(uri)
    family = _family_for_uri(uri, files_root=files_root)
    sha = _read_sha(uri, files_root=files_root)
    return ScoreboardRef(uri=uri, slug=slug, family=family, sha256=sha)


__all__ = ["ScoreboardRef", "resolve_scoreboard_ref"]
