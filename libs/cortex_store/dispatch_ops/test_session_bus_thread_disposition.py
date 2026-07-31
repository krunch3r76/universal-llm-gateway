"""Tests for session-close agent-bus thread disposition preflight fields."""

from __future__ import annotations

from typing import Any

from cortex_store.dispatch_ops._session_bus_thread_disposition import (
    active_bus_threads_in_entity_ids,
    bus_thread_disposition_preflight_fields,
    parse_bus_thread_refs,
)


def test_parse_bus_thread_refs_empty() -> None:
    assert parse_bus_thread_refs(None) == []
    assert parse_bus_thread_refs([]) == []


def test_parse_bus_thread_refs_agent_bus_and_numeric() -> None:
    refs = parse_bus_thread_refs(
        ["todo:foo", "agent-bus:5366", "5367", "agent-bus:5366#turn-3"]
    )
    assert refs == ["5366", "5367"]


def test_active_thread_in_entity_ids_emits_warning() -> None:
    def lookup(thread_id: str) -> dict[str, Any]:
        return {"id": thread_id, "status": "active"}

    fields = bus_thread_disposition_preflight_fields(
        ["agent-bus:5366"], status_lookup=lookup
    )
    assert fields["active_bus_threads_in_entity_ids"] == [
        {"thread_id": "5366", "status": "active"}
    ]
    warning = fields.get("bus_thread_disposition_warning")
    assert isinstance(warning, str)
    assert "bus_thread_disposition.required" in warning
    assert "agent-bus:5366" in warning


def test_closed_thread_no_warning() -> None:
    def lookup(thread_id: str) -> dict[str, Any]:
        return {"id": thread_id, "status": "closed"}

    fields = bus_thread_disposition_preflight_fields(
        ["5366"], status_lookup=lookup
    )
    assert fields["active_bus_threads_in_entity_ids"] == []
    assert "bus_thread_disposition_warning" not in fields


def test_missing_thread_no_warning() -> None:
    fields = bus_thread_disposition_preflight_fields(
        ["agent-bus:9999"], status_lookup=lambda _: None
    )
    assert fields["active_bus_threads_in_entity_ids"] == []
    assert "bus_thread_disposition_warning" not in fields


def test_thread_480_excluded_even_when_active() -> None:
    def lookup(thread_id: str) -> dict[str, Any]:
        return {"id": thread_id, "status": "active"}

    pending = active_bus_threads_in_entity_ids(
        ["480", "agent-bus:480"], status_lookup=lookup
    )
    assert pending == []
