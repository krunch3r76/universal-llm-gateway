from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from .models import AgentName

# Self-addressed guard removed — root cause fixed (fix-1, todo:agent-bus-from-attribution-root-cause).

# Free-form strings. The agent_bus docstring documents a `namespace:value`
# convention (e.g. `project:claudeburst`, `type:bug`, `agent:cursor`) but
# nothing is enforced here — tags are normalized in the DB layer
# (strip + lowercase + dedupe) via _normalize_tags before storage.

# Agent-bus convention: turns are short briefings with sidecar markdown
# references. Full documents belong in notes/system/threads/ and are
# referenced, not inlined. A per-request escape hatch exists for rare
# web-agent communications that require inline long form.
MAX_TURN_BODY_CHARS = 8_000


def body_too_large_envelope(*, limit: int, body_chars: int) -> dict[str, object]:
    """Structured 413 detail for oversized turn bodies."""
    return {
        "reason": "body_too_large",
        "limit_chars": limit,
        "body_chars": body_chars,
        "suggestion": "sidecar_markdown_or_allow_long_body",
        "message": (
            f"Turn body exceeds {limit:,} chars. "
            "Agent-bus convention: short briefing + sidecar markdown reference. "
            "Write long content to notes/system/threads/<thread>-<subject>.md "
            "and reference it in a brief body. If inline long-form delivery is "
            "required for this recipient, retry with allow_long_body=true."
        ),
    }


def turn_body_limit_error(
    body: str, *, allow_long_body: bool = False
) -> dict[str, object] | None:
    """Return the structured limit error unless this request opts into long form."""
    if allow_long_body or len(body) <= MAX_TURN_BODY_CHARS:
        return None
    return body_too_large_envelope(
        limit=MAX_TURN_BODY_CHARS,
        body_chars=len(body),
    )


def post_continuation_misuse_error(
    slug: str, after_turn: int | None
) -> dict[str, object] | None:
    """Reject continuation-shaped misuse of the create-thread (`post`) path.

    `post` (POST /threads/with-turn) always mints a NEW thread. Two arg shapes
    signal the caller actually meant to continue an existing thread via `reply`
    (POST /turns); silently creating a forked thread is the 1140->1142 footgun.

      - slug all-digits -> a thread ID jammed into the slug field
      - after_turn set  -> a continuation field; inert on create (the unread
                           check in create_thread_with_turn runs against the
                           freshly created empty thread, so it never fires)

    Returns a structured 400 envelope, or None for a legitimate new-thread post.
    """
    if slug.isdigit():
        return {
            "reason": "slug_looks_like_thread_id",
            "slug": slug,
            "message": (
                f"slug {slug!r} is all digits and looks like a thread ID. "
                "post always creates a NEW thread (id is auto-assigned; slug is "
                "a human label). To continue an existing thread use "
                "reply(thread=<id>, after_turn=<n>). To create a new thread, "
                "pick a descriptive non-numeric slug."
            ),
            "suggestion": "use_reply_to_continue",
        }
    if after_turn is not None:
        return {
            "reason": "after_turn_not_valid_on_post",
            "after_turn": after_turn,
            "message": (
                "after_turn is a continuation field and has no effect on post, "
                "which always creates a new thread. To continue an existing "
                "thread use reply(thread=<id>, after_turn=<n>)."
            ),
            "suggestion": "use_reply_to_continue",
        }
    return None


# --- Attachment schemas ---


class AttachmentCreate(BaseModel):
    """Metadata for a file attached to a turn."""

    filename: str
    path: str
    mime_type: str | None = None
    size_bytes: int | None = None
    sha256: str | None = None


class Attachment(AttachmentCreate):
    """Stored attachment with database ID."""

    id: int


# --- Turn/Thread status enums ---


class TurnStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"
    SUPERSEDED = "superseded"
    WAITING = "waiting"


class ThreadStatus(StrEnum):
    ACTIVE = "active"
    BLOCKED = "blocked"
    WAITING = "waiting"
    CLOSED = "closed"


# --- Turn schemas ---


class TurnCreate(BaseModel):
    model_config = {"populate_by_name": True}

    thread: str
    thread_slug: str | None = None
    from_agent: AgentName = Field(alias="from")
    to: AgentName
    subject: str
    body: str
    allow_long_body: bool = False
    status: TurnStatus = TurnStatus.OPEN
    after_turn: int | None = None
    supersedes_turn: int | None = None
    attachments: list[AttachmentCreate] | None = None


class TurnCreated(BaseModel):
    id: int
    thread: str
    turn_number: int
    created_at: datetime


class Turn(BaseModel):
    model_config = {"populate_by_name": True, "serialize_by_alias": True}

    id: int
    thread: str
    turn_number: int
    from_agent: AgentName = Field(alias="from", serialization_alias="from")
    to: AgentName
    subject: str
    body: str | None = None
    status: TurnStatus
    supersedes_turn: int | None = None
    created_at: datetime
    read_at: datetime | None = None
    attachments: list[Attachment] | None = None


class TurnList(BaseModel):
    turns: list[Turn]


class TurnUpdate(BaseModel):
    subject: str | None = None
    body: str | None = None
    append: str | None = None


class TurnStatusUpdate(BaseModel):
    status: TurnStatus
    supersedes_turn: int | None = None


# --- Thread schemas ---


class DispatchLinkSummary(BaseModel):
    """Single dispatch link attached to a lifecycle-managed thread."""

    execution_id: str
    pipeline_id: str
    linked_at: datetime
    terminal_status: str | None = None
    delivery_at: datetime | None = None


class ThreadCreate(BaseModel):
    id: str | None = None
    slug: str
    summary: str | None = None
    tags: list[str] = Field(default_factory=list)
    # When set, opts this thread into lifecycle management from creation.
    # None = no lifecycle (backward-compat default).
    lifecycle_state: str | None = None


class ThreadDetail(BaseModel):
    id: str
    slug: str
    status: ThreadStatus
    summary: str | None = None
    turn_count: int
    unread_count: int
    last_subject: str | None = None
    last_turn_from: str | None = None
    last_turn_to: str | None = None
    tags: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    bus_lifecycle_state: str | None = None
    dispatch_links: list[DispatchLinkSummary] = Field(default_factory=list)


class ThreadSummaryResponse(BaseModel):
    id: str
    slug: str
    status: ThreadStatus
    summary: str | None = None
    turn_count: int
    unread_count: int
    recent_subjects: list[str]
    tags: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class ThreadListResponse(BaseModel):
    threads: list[ThreadDetail]


class ThreadUpdate(BaseModel):
    status: ThreadStatus | None = None
    summary: str | None = None
    # None = leave tags unchanged. [] = clear all. [...] = replace with the new set.
    tags: list[str] | None = None


class TurnDelete(BaseModel):
    force: bool = False


class ThreadDelete(BaseModel):
    force: bool = False


class ThreadRename(BaseModel):
    new_id: str


class ThreadWithTurnCreate(BaseModel):
    """Atomic thread creation with first turn - no partial state possible."""

    model_config = {"populate_by_name": True}

    slug: str
    summary: str | None = None
    from_agent: AgentName = Field(alias="from")
    to: AgentName
    subject: str
    body: str
    allow_long_body: bool = False
    status: TurnStatus = TurnStatus.OPEN
    after_turn: int | None = None
    attachments: list[AttachmentCreate] | None = None
    tags: list[str] = Field(default_factory=list)


class ThreadWithTurnCreated(BaseModel):
    """Response for atomic thread+turn creation."""

    thread: ThreadDetail
    turn: TurnCreated


class ThreadClose(BaseModel):
    """Atomic close: marks all turns read + sets status to closed."""

    summary: str | None = None
    mark_all_read: bool = True


class DispatchAdmit(BaseModel):
    """Payload for POST /threads/{id}/dispatch-admit."""

    execution_id: str
    pipeline_id: str
    caller_agent: str | None = None
