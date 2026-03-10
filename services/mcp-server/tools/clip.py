"""Web clip tools — discover and read browser-clipped content.

Clips are saved by the /clip endpoint (called from a browser bookmarklet)
as markdown files with YAML frontmatter in /data/files/clips/.

When the bookmarklet sends HTML (e.g. full page or selection), content is
normalized with trafilatura to strip boilerplate so stored clips are lean
and LLM-friendly.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

import trafilatura

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger(__name__)

_CLIPS_DIR = Path("/data/files/clips")
_CLIP_ID_PATTERN = re.compile(r"^[\w\-]+\.md$")
_HTML_TAG_RE = re.compile(r"</?[a-zA-Z][^>]*>")
_ALREADY_DOCUMENT_RE = re.compile(r"<!DOCTYPE\s+html\b", re.IGNORECASE)
_ALREADY_HTML_ROOT_RE = re.compile(r"<html[\s>]", re.IGNORECASE)
_MIN_EXTRACT_CHARS = 50
_MIN_EXTRACT_RATIO = 0.3


def normalize_clip_content(content: str) -> tuple[str, bool]:
    """Strip HTML boilerplate from clip content when it looks like HTML.

    If content appears to be HTML (tag-like patterns), run trafilatura extraction
    and return (clean_text, True). Otherwise return (content_unchanged, False).
    Avoids double-wrapping when content is already a full HTML document.
    """
    if not content.strip():
        return content, False
    if not _HTML_TAG_RE.search(content):
        return content, False
    if _ALREADY_DOCUMENT_RE.search(content) or _ALREADY_HTML_ROOT_RE.search(content):
        wrapped = content
    else:
        wrapped = (
            "<!DOCTYPE html><html><head><meta charset='utf-8'></head><body>"
            f"{content}</body></html>"
        )
    extracted = trafilatura.extract(
        wrapped,
        include_links=True,
        include_tables=True,
        output_format="txt",
    )
    if extracted is None or not extracted.strip():
        logger.warning(
            "normalize_clip_content: trafilatura returned empty, keeping original"
        )
        return content, False
    extracted_clean = extracted.strip()
    original_len = len(content)
    extracted_len = len(extracted_clean)
    ratio = extracted_len / original_len if original_len > 0 else 0.0
    if extracted_len < _MIN_EXTRACT_CHARS or ratio < _MIN_EXTRACT_RATIO:
        logger.info(
            "normalize_clip_content: extraction ratio %.2f (%d/%d), falling back to original",
            ratio,
            extracted_len,
            original_len,
        )
        return content, False
    logger.info(
        "normalize_clip_content: extracted %d chars from %d (HTML stripped)",
        extracted_len,
        original_len,
    )
    return extracted_clean, True


def _parse_frontmatter(head: str) -> dict[str, str]:
    """Extract YAML frontmatter fields from the first lines of a clip file."""
    meta: dict[str, str] = {}
    in_frontmatter = False
    for line in head.splitlines():
        stripped = line.strip()
        if stripped == "---":
            if in_frontmatter:
                break
            in_frontmatter = True
            continue
        if in_frontmatter and ":" in stripped:
            key, _, value = stripped.partition(":")
            meta[key.strip()] = value.strip().strip('"')
    return meta


def register_clip_tools(mcp: FastMCP) -> None:
    """Register clip discovery and reading tools on *mcp*."""

    @mcp.tool()
    def list_clips(limit: int = 20) -> dict[str, list[dict[str, str]] | str]:
        """List saved web clips, most recent first.

        Clips are pages captured from your browser via the bookmarklet.
        Each clip has metadata (url, title, timestamp) and content.

        Use read_clip() with the clip_id to read the full content.

        Args:
            limit: Maximum number of clips to return (default 20).

        Returns:
            {"clips": [{"clip_id", "title", "url", "clipped_at", "selected", "chars"}, ...]}
        """
        if not _CLIPS_DIR.exists():
            return {"clips": []}

        paths = sorted(
            _CLIPS_DIR.glob("*.md"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

        resolved_root = _CLIPS_DIR.resolve()
        clips: list[dict[str, str]] = []
        for path in paths[:limit]:
            try:
                if path.resolve().parent != resolved_root:
                    logger.warning("Skipping clip outside sandbox: %s", path.name)
                    continue
                with path.open("r", encoding="utf-8", errors="replace") as f:
                    head = "".join(f.readline() for _ in range(10))
                meta = _parse_frontmatter(head)
                meta["clip_id"] = path.name
                clips.append(meta)
            except OSError as exc:
                logger.warning("Failed to read clip %s: %s", path.name, exc)

        logger.info("list_clips: %d clips", len(clips))
        return {"clips": clips}

    @mcp.tool()
    def read_clip(clip_id: str) -> dict[str, str]:
        """Read a web clip by its clip_id.

        Returns the full content including frontmatter metadata.
        Use list_clips() to discover available clip_ids.

        Args:
            clip_id: The clip filename (e.g. "upwork-job-posting-1709932800.md").

        Returns:
            {"content": "<full clip content>", "clip_id": "<clip_id>"}
        """
        if not _CLIP_ID_PATTERN.match(clip_id):
            return {"error": "Invalid clip_id format"}

        clip_path = _CLIPS_DIR / clip_id
        if not clip_path.exists():
            return {"error": f"Clip not found: {clip_id}"}

        content = clip_path.read_text(encoding="utf-8", errors="replace")
        logger.info("read_clip: %s (%d chars)", clip_id, len(content))
        return {"content": content, "clip_id": clip_id}
