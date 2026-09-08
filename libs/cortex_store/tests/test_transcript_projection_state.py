"""Offline tests for transcript_projection_state (P3-P5, AC-2/AC-3)."""

from __future__ import annotations

import json

import pytest

from cortex_store.transcript_projection_state import (
    empty_state,
    load_state,
    merge_window_update,
    state_transform_merge,
)


@pytest.mark.offline
def test_p3_idempotent_merge() -> None:
    state = empty_state("10223", ["10303"])
    patch = {
        "lane": "10223",
        "binding": "dominant_write",
        "turn_count": 3,
        "closed_hi": 2,
        "cells": [{"turn_lo": 0, "turn_hi": 2, "note": "a", "facts": {}}],
    }
    added: list = []
    updated: list = []
    merge_window_update(state, "uuid-1", patch, cells_added=added, cells_updated=updated)
    text1 = json.dumps(state)
    merge_window_update(state, "uuid-1", patch, cells_added=[], cells_updated=[])
    text2 = json.dumps(state)
    assert text1 == text2


@pytest.mark.offline
def test_p5_bus_join_backfill_field_mutable() -> None:
    state = empty_state("10223", [])
    cell = {
        "turn_lo": 0,
        "turn_hi": 2,
        "boundary": {"turn_number": None, "kind": "CHECKPOINT"},
        "note": "x",
        "facts": {},
    }
    patch = {"cells": [cell], "closed_hi": 2, "turn_count": 2}
    merge_window_update(state, "u1", patch, cells_added=[], cells_updated=[])
    cell2 = dict(cell)
    cell2["boundary"] = {"turn_number": 5, "kind": "CHECKPOINT"}
    patch2 = {"cells": [cell2], "closed_hi": 2, "turn_count": 2}
    updated: list = []
    merge_window_update(state, "u1", patch2, cells_added=[], cells_updated=updated)
    assert state["windows"]["u1"]["cells"][0]["boundary"]["turn_number"] == 5


@pytest.mark.offline
def test_state_transform_merge_increments_runs() -> None:
    out = state_transform_merge(
        "",
        thread="10223",
        children=["10303"],
        window_updates={},
        anchor_mismatches=[],
        last_run={"parsed": 0},
        updated_at="2026-09-08T00:00:00+00:00",
    )
    data = load_state(out)
    assert data["runs"] == 1
    assert data["schema"] == "transcript-projection/v1"
