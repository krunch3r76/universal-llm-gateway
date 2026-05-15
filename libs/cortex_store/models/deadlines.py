"""Deadline-shape Pydantic models."""

from __future__ import annotations

from pydantic import BaseModel

from .assertions import ActionHint


class DeadlineItem(BaseModel):
    matter_id: str
    matter_name: str
    deadline_id: str | None = None
    deadline_name: str
    deadline_date: str | None = None
    deadline_description: str | None = None
    urgency: str | None = None
    outcome: str | None = None


class DeadlineList(BaseModel):
    items: list[DeadlineItem]
    action_hints: list[ActionHint] | None = None
