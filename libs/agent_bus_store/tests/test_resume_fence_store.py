"""Unit tests for resume fence journal fold."""

from __future__ import annotations

import pytest

from agent_bus_store.db import init_db
from agent_bus_store.resume_fence_store import (
    append_fence_event,
    find_open_fence,
    fold_fence,
    mint_fence_id,
    release_fence,
)

pytestmark = pytest.mark.offline


@pytest.fixture()
def fence_db(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    monkeypatch.setenv("AGENT_BUS_EVENTS_ENABLED", "false")
    init_db()
    yield


def test_fold_armed_poured_released(fence_db) -> None:
    fid = mint_fence_id()
    append_fence_event(
        fence_id=fid,
        root_thread="10223",
        event="armed",
        transcript_id="tab-1",
        payload={"source": "test"},
    )
    append_fence_event(
        fence_id=fid,
        root_thread="10223",
        event="poured",
        transcript_id="tab-1",
        payload={"bundle_bytes": 100},
    )
    folded = fold_fence(fid)
    assert folded is not None
    assert folded.state == "poured"
    release_fence(fence_id=fid, release_turn=42)
    assert fold_fence(fid).state == "released"


def test_find_open_fence_adopts_hook_armed_fence(fence_db) -> None:
    hook_fid = mint_fence_id()
    append_fence_event(
        fence_id=hook_fid,
        root_thread="10223",
        event="armed",
        transcript_id="tab-hook",
        payload={"source": "hook_prompt", "read_set": {"readable": {}}},
    )
    adopted = find_open_fence(root_thread="10223", transcript_id=None)
    assert adopted == hook_fid


def test_find_open_fence_adoption_ambiguous_returns_none(fence_db) -> None:
    for idx in range(2):
        fid = mint_fence_id()
        append_fence_event(
            fence_id=fid,
            root_thread="10223",
            event="armed",
            transcript_id=f"tab-{idx}",
            payload={"source": "hook_prompt"},
        )
    assert find_open_fence(root_thread="10223", transcript_id=None) is None


def test_release_idempotent_when_not_open(fence_db) -> None:
    fid = mint_fence_id()
    append_fence_event(
        fence_id=fid,
        root_thread="10223",
        event="armed",
    )
    release_fence(fence_id=fid, release_turn=1)
    again = release_fence(fence_id=fid, release_turn=2)
    assert again is not None
    assert again.state == "released"
