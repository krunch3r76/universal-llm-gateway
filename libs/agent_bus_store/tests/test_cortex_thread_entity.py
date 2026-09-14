"""Offline tests for role:root thread entity auto-mint (R19)."""

from __future__ import annotations

from typing import Any

import pytest
from agent_bus_store.cortex_thread_entity import (
    ensure_thread_entity,
    is_role_root_thread,
    mint_thread_entity,
    thread_entity_id,
)
from agent_bus_store.db import create_thread, init_db
from agent_bus_store.db.threads import add_tags

pytestmark = pytest.mark.offline


@pytest.fixture()
def bus_db(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    init_db()


@pytest.fixture()
def capture_mint_failed(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def _capture(**kwargs: Any) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(
        "agent_bus_store.events.thread_entity_mint.emit_thread_entity_mint_failed",
        _capture,
    )
    return calls


def _install_cortex_stub(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {"entities": set(), "edges": set()}

    def _dispatch(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if tool == "entity_get":
            eid = str(arguments["entity_id"])
            if eid in state["entities"]:
                return {"id": eid}
            return {"error": "not found", "status_code": 404}
        if tool == "entity_create":
            eid = str(arguments["id"])
            if eid in state["entities"]:
                return {"error": "conflict", "status_code": 409}
            state["entities"].add(eid)
            return {"id": eid}
        if tool == "relationship_create":
            key = (
                arguments["source_id"],
                arguments["target_id"],
                arguments["type_id"],
            )
            if key in state["edges"]:
                return {"was_new": False}
            state["edges"].add(key)
            return {"was_new": True}
        raise AssertionError(f"unexpected tool {tool}")

    monkeypatch.setattr(
        "agent_bus_store.cortex_thread_entity._cortex_dispatch",
        _dispatch,
    )
    return state


class TestIsRoleRootThread:
    def test_detects_role_root(self) -> None:
        assert is_role_root_thread(["role:root", "project:ulg"])

    def test_rejects_work_thread(self) -> None:
        assert not is_role_root_thread(["project:ulg"])


class TestMintThreadEntity:
    def test_mints_entity_and_hub_edge(self, monkeypatch: pytest.MonkeyPatch) -> None:
        state = _install_cortex_stub(monkeypatch)
        hub_id = "document:42-continuity"
        state["entities"].add(hub_id)

        result = mint_thread_entity("42", "continuity-house")

        assert result["created"] is True
        assert thread_entity_id("42") in state["entities"]
        assert ("thread:42", hub_id, "references") in state["edges"]

    def test_idempotent_second_run(self, monkeypatch: pytest.MonkeyPatch) -> None:
        state = _install_cortex_stub(monkeypatch)
        first = mint_thread_entity("7", "house")
        second = mint_thread_entity("7", "house")
        assert first["created"] is True
        assert second["created"] is False
        assert len([e for e in state["entities"] if e == "thread:7"]) == 1

    def test_skips_edge_when_hub_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        state = _install_cortex_stub(monkeypatch)
        result = mint_thread_entity("9", "orphan-root")
        assert result["created"] is True
        assert state["edges"] == set()


class TestEnsureThreadEntityHook:
    def test_create_thread_role_root_mints(self, bus_db, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[tuple[str, str, list[str] | None]] = []

        def _ensure(thread_id: str, slug: str, tags: list[str] | None) -> None:
            calls.append((thread_id, slug, tags))

        monkeypatch.setattr(
            "agent_bus_store.cortex_thread_entity.ensure_thread_entity",
            _ensure,
        )
        row = create_thread(thread_id=None, slug="root-house", tags=["role:root"])
        assert row is not None
        assert calls == [(row["id"], "root-house", ["role:root"])]

    def test_create_thread_work_skips_mint(self, bus_db, monkeypatch: pytest.MonkeyPatch) -> None:
        mint_calls: list[tuple[str, str]] = []

        def _mint(thread_id: str, slug: str, **_kwargs: Any) -> dict[str, Any]:
            mint_calls.append((thread_id, slug))
            return {"created": True}

        monkeypatch.setattr(
            "agent_bus_store.cortex_thread_entity.mint_thread_entity",
            _mint,
        )
        create_thread(thread_id=None, slug="work-lane", tags=["project:ulg"])
        assert mint_calls == []

    def test_add_tags_role_root_mints(self, bus_db, monkeypatch: pytest.MonkeyPatch) -> None:
        row = create_thread(thread_id=None, slug="promote-me", tags=["project:ulg"])
        assert row is not None
        calls: list[tuple[str, str, list[str] | None]] = []

        def _ensure(thread_id: str, slug: str, tags: list[str] | None) -> None:
            calls.append((thread_id, slug, tags))

        monkeypatch.setattr(
            "agent_bus_store.cortex_thread_entity.ensure_thread_entity",
            _ensure,
        )
        add_tags(row["id"], ["role:root"])
        assert calls
        assert calls[-1][2] == ["project:ulg", "role:root"]

    def test_cortex_failure_does_not_break_create(
        self, bus_db, monkeypatch: pytest.MonkeyPatch, capture_mint_failed: list[dict]
    ) -> None:
        def _boom(*_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("cortex down")

        monkeypatch.setattr(
            "agent_bus_store.cortex_thread_entity.mint_thread_entity",
            _boom,
        )
        row = create_thread(thread_id=None, slug="resilient", tags=["role:root"])
        assert row is not None
        assert capture_mint_failed
        assert capture_mint_failed[0]["thread"] == row["id"]

    def test_ensure_emits_on_mint_error(
        self, monkeypatch: pytest.MonkeyPatch, capture_mint_failed: list[dict]
    ) -> None:
        def _fail(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            raise RuntimeError("dispatch failed")

        monkeypatch.setattr(
            "agent_bus_store.cortex_thread_entity.mint_thread_entity",
            _fail,
        )
        ensure_thread_entity("55", "house", ["role:root"])
        assert capture_mint_failed[0]["entity_id"] == "thread:55"
