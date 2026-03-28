from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class AgentName(StrEnum):
    WEB = "web"
    API = "api"
    CURSOR = "cursor"
    KAYWAN = "kaywan"
    ALL = "all"


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
