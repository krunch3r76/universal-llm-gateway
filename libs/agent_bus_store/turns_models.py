from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from .models import AgentName

# Free-form strings. The agent_bus docstring documents a `namespace:value`
# convention (e.g. `project:claudeburst`, `type:bug`, `agent:cursor`) but
# nothing is enforced here — tags are normalized in the DB layer
# (strip + lowercase + dedupe) via _normalize_tags before storage.

# Agent-bus convention: turns are short briefings with sidecar markdown
# references. Full documents belong in notes/system/threads/ and are
# referenced, not inlined. Enforced at the REST surface — see the
# RequestValidationError handler in server.py for the structured 413 envelope.
MAX_TURN_BODY_CHARS = 8_000

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
    body: str = Field(max_length=MAX_TURN_BODY_CHARS)
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
    body: str = Field(max_length=MAX_TURN_BODY_CHARS)
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
