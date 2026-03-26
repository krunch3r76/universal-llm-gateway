"""Pydantic models for the entity_status roll-up endpoint."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from src.models import AssertionItem, EntityStatus, RelationshipItem

StalenessSignal = Literal["active", "recent", "aging", "stale", "dormant"]


class FreshnessInfo(BaseModel):
    last_assertion_at: str | None = None
    last_journal_mention_at: str | None = None
    last_entity_update_at: str | None = None
    last_accessed_by: str | None = None
    staleness_hours: float
    staleness_signal: StalenessSignal


class SessionMention(BaseModel):
    id: int
    timestamp: str
    agent: str
    summary: str
    decisions: list[str] | None = None


class TodoReference(BaseModel):
    id: str
    title: str
    priority: str | None = None


class ThreadReference(BaseModel):
    thread: str
    slug: str
    unread: int


class InFlightRequest(BaseModel):
    request_id: str | None = None
    operation: str
    phase: str | None = None
    started_at: str | None = None
    elapsed_seconds: float | None = None
    last_blocking_reason: str | None = None


class RecentCompletion(BaseModel):
    request_id: str | None = None
    operation: str
    completed_at: str | None = None
    duration_ms: float | None = None
    status: str


class RecentFailure(BaseModel):
    request_id: str | None = None
    operation: str
    failed_at: str | None = None
    error: str | None = None


class InFlightSection(BaseModel):
    service_last_started: str | None = None
    active_requests: list[InFlightRequest] = Field(default_factory=list)
    recent_completions: list[RecentCompletion] = Field(default_factory=list)
    recent_failures: list[RecentFailure] = Field(default_factory=list)


class StatusEntity(BaseModel):
    id: str
    type: str
    name: str
    description: str | None = None
    status: EntityStatus | None = None
    aliases: list[str] | None = None
    attributes: dict[str, Any] | None = None


class StatusSummary(BaseModel):
    active_assertion_count: int
    historical_assertion_count: int
    relationship_count: int
    todo_count: int
    thread_count: int
    session_mention_count: int


class EntityStatusResponse(BaseModel):
    entity: StatusEntity
    freshness: FreshnessInfo
    active_assertions: list[AssertionItem]
    historical_assertions: list[AssertionItem] = Field(default_factory=list)
    relationships: dict[str, list[RelationshipItem]]
    recent_sessions: list[SessionMention]
    open_todos: list[TodoReference]
    active_threads: list[ThreadReference]
    in_flight: InFlightSection | None = None
    summary: StatusSummary
