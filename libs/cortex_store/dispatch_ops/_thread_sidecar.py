"""Render + persist on-behalf thread sidecar markdown under cortex _FILES_ROOT."""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime

from ._shared import _FILES_ROOT

_SIDECAR_SUBDIR = ("notes", "system", "threads")
_SLUG_MAXLEN = 60


def _slugify(subject: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (subject or "reply").lower()).strip("-")
    return (s[:_SLUG_MAXLEN].rstrip("-")) or "reply"


def thread_sidecar_uri(thread: str, slug: str) -> str:
    return f"cortex://notes/system/threads/{thread}-{slug}.md"


def content_sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def render_thread_sidecar_markdown(
    *,
    thread: str,
    subject: str,
    content: str,
    from_agent: str | None,
    execution_id: str | None,
    sha256: str,
    body_chars: int,
    oversized: bool,
) -> str:
    fm = [
        "---",
        f"thread: {thread}",
        f"execution_id: {execution_id or ''}",
        f"from_agent: {from_agent or 'dispatch'}",
        f"subject: {subject}",
        "durable_copy: true",
        f"delivery_mode: {'sidecar' if oversized else 'inline'}",
        f"oversized: {str(oversized).lower()}",
        f"sha256: {sha256}",
        f"body_chars: {body_chars}",
        f"written_at: {datetime.now(UTC).isoformat()}",
        "---",
        "",
    ]
    return "\n".join(fm) + content


def write_thread_sidecar(thread: str, slug: str, file_content: str) -> str:
    path = _FILES_ROOT.joinpath(*_SIDECAR_SUBDIR) / f"{thread}-{slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(file_content, encoding="utf-8")
    return str(path)
