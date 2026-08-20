"""Render + persist thread sidecar markdown under cortex _FILES_ROOT.

Writes funnel through ``durable_io.atomic`` (flock + temp+fsync+replace +
retain). Callers — send_sidecar, body_auto_spill, stargate on_behalf,
charter-runner state_close — must not grow a second ``Path.write_text``.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime

from durable_io.atomic import durable_write_text

from ._shared import _FILES_ROOT

_SIDECAR_SUBDIR = ("notes", "system", "threads")
_SLUG_MAXLEN = 60
MAX_SIDECAR_CONTENT_CHARS = 256 * 1024


class SidecarContentTooLargeError(Exception):
    """Raised when sidecar_content exceeds MAX_SIDECAR_CONTENT_CHARS."""

    def __init__(self, *, body_chars: int) -> None:
        self.body_chars = body_chars
        super().__init__(
            f"sidecar_content exceeds {MAX_SIDECAR_CONTENT_CHARS:,} chars "
            f"({body_chars:,} provided)"
        )


class SidecarWriteError(Exception):
    """Raised when the durable sidecar file could not be written."""


def _slugify(subject: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (subject or "reply").lower()).strip("-")
    return (s[:_SLUG_MAXLEN].rstrip("-")) or "reply"


def resolve_sidecar_slug(*, sidecar_slug: str | None, subject: str) -> str:
    """Resolve the filename slug for ``<thread>-<slug>.md``."""
    if sidecar_slug is not None and sidecar_slug.strip():
        return _slugify(sidecar_slug)
    return _slugify(subject)


def thread_sidecar_uri(thread: str, slug: str) -> str:
    return f"cortex://notes/system/threads/{thread}-{slug}.md"


def content_sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def append_sidecar_pointer_line(body: str, *, sidecar_uri: str) -> str:
    """Append the deterministic E4 trailing pointer line to a turn body."""
    trimmed = body.rstrip()
    if trimmed:
        return f"{trimmed}\n\nSidecar: {sidecar_uri}"
    return f"Sidecar: {sidecar_uri}"


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
    durable_write_text(path, file_content, retain_store_root=_FILES_ROOT)
    return str(path)


@dataclass(frozen=True)
class ThreadSidecarWriteResult:
    uri: str
    sha256: str
    path: str
    body_chars: int
    slug: str


def write_thread_sidecar_for_send(
    *,
    thread: str,
    subject: str,
    content: str,
    from_agent: str,
    sidecar_slug: str | None = None,
    execution_id: str | None = None,
    oversized: bool = False,
) -> ThreadSidecarWriteResult:
    """Hoisted writer primitive shared by send, on_behalf, and cortex ops."""
    if len(content) > MAX_SIDECAR_CONTENT_CHARS:
        raise SidecarContentTooLargeError(body_chars=len(content))

    slug = resolve_sidecar_slug(sidecar_slug=sidecar_slug, subject=subject)
    digest = content_sha256(content)
    md = render_thread_sidecar_markdown(
        thread=thread,
        subject=subject,
        content=content,
        from_agent=from_agent,
        execution_id=execution_id,
        sha256=digest,
        body_chars=len(content),
        oversized=oversized,
    )
    try:
        path = write_thread_sidecar(thread, slug, md)
    except OSError as exc:
        raise SidecarWriteError(str(exc)) from exc
    return ThreadSidecarWriteResult(
        uri=thread_sidecar_uri(thread, slug),
        sha256=digest,
        path=path,
        body_chars=len(content),
        slug=slug,
    )
