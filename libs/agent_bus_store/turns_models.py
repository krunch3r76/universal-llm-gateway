from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from .models import AgentName

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


class ThreadCreate(BaseModel):
    id: str | None = None
    slug: str
    summary: str | None = None


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
    created_at: datetime
    updated_at: datetime


class ThreadSummaryResponse(BaseModel):
    id: str
    slug: str
    status: ThreadStatus
    summary: str | None = None
    turn_count: int
    unread_count: int
    recent_subjects: list[str]
    created_at: datetime
    updated_at: datetime


class ThreadListResponse(BaseModel):
    threads: list[ThreadDetail]


class ThreadUpdate(BaseModel):
    status: ThreadStatus | None = None
    summary: str | None = None


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
    status: TurnStatus = TurnStatus.OPEN
    after_turn: int | None = None
    attachments: list[AttachmentCreate] | None = None


class ThreadWithTurnCreated(BaseModel):
    """Response for atomic thread+turn creation."""

    thread: ThreadDetail
    turn: TurnCreated


class ThreadClose(BaseModel):
    """Atomic close: marks all turns read + sets status to closed."""

    summary: str | None = None
    mark_all_read: bool = True
