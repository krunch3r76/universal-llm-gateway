from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

# Free-form string — any agent name or pipeline-generated sender (e.g.
# "frontier:openai") is valid.  Historical enum values ("web", "cursor",
# etc.) are still valid; no new validation is enforced at the model layer.
AgentName = str


class MessageCreate(BaseModel):
    model_config = {"populate_by_name": True}

    from_agent: AgentName = Field(alias="from")
    to: AgentName
    thread: str
    body: str


class MessageCreated(BaseModel):
    id: int
    timestamp: datetime


class Message(BaseModel):
    model_config = {"populate_by_name": True, "serialize_by_alias": True}

    id: int
    from_agent: AgentName = Field(alias="from", serialization_alias="from")
    to: AgentName
    thread: str
    body: str
    timestamp: datetime
    read: bool


class MessageList(BaseModel):
    messages: list[Message]


class ThreadSummary(BaseModel):
    thread: str
    total: int
    unread: int
    latest: datetime


class ThreadList(BaseModel):
    threads: list[ThreadSummary]
